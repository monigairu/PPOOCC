"""3段パイプライン ask() の分岐テスト（DESIGN §1-2・§6）。

②③④（LLM・Check Grounding）と①（load_f3）は monkeypatch で差し替え、
ゲート判断・棄却理由・Evidence 変換というパイプライン自体のロジックを検証する。
外部APIを叩く実測はミニ評価（qa_cases.yaml・フェーズ1完了条件）が担う。
"""
import pytest

from apps.backend.app.inquiry import pipeline
from apps.backend.app.inquiry.models import (
    GeneratedAnswer,
    GroundingResult,
    SufficiencyResult,
)
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    KnowledgeSearchError,
)


def _record(record_id="03_KT_1G_01_0002", seq=1, direction="nuro", content="共用対象工事・削減金額の根拠が不明。"):
    """load_f3 が返す形のレコード（1行=1メッセージ・D-9）。"""
    return {
        "id": record_id,
        "_doc_id": f"{record_id}_{seq:02d}",
        "sheet_name": "KNI_1G_01",
        "round": "1",
        "message_direction": direction,
        "message_content": content,
        "utility_name": "関東電力",
        "_rerank_score": 0.9,
    }


@pytest.fixture
def patch_stages(monkeypatch):
    """全ステージを正常系で差し替え、テストごとに上書きできるようにする。"""
    records = [_record(seq=1, direction="nuro"), _record(seq=2, direction="denryoku")]
    monkeypatch.setattr(pipeline, "load_f3", lambda **kw: records)
    monkeypatch.setattr(
        pipeline, "check_sufficiency",
        lambda q, r: SufficiencyResult(
            sufficient=True, usable_record_ids=["03_KT_1G_01_0002"], reason="直接回答あり"
        ),
    )
    monkeypatch.setattr(
        pipeline, "generate_answer",
        lambda q, r: GeneratedAnswer(
            answer="削減金額の根拠が必要との記録があります [F3#03_KT_1G_01_0002]",
            cited_record_ids=["03_KT_1G_01_0002"],
        ),
    )
    monkeypatch.setattr(
        pipeline, "check_grounding",
        lambda a, r: GroundingResult(score=0.9),
    )
    return records


class TestAnsweredPath:
    def test_happy_path(self, patch_stages):
        result = pipeline.ask("実施費用低減策の記載粒度は？", "関東電力")
        assert result.status == "answered"
        assert result.grounding_score == 0.9
        assert result.abstain_reason is None
        # 引用されたレコードの両メッセージ（nuro確認/denryoku回答）が根拠になる
        assert [e.citation_key for e in result.evidences] == [
            ("03_KT_1G_01_0002", 1, "nuro"),
            ("03_KT_1G_01_0002", 1, "denryoku"),
        ]

    def test_evidence_mapping(self, patch_stages):
        """Evidence のフィールド対応（models.py docstring の対応表）"""
        result = pipeline.ask("q", "関東電力")
        ev = result.evidences[0]
        assert ev.sheet == "KNI_1G_01"
        assert ev.score == 0.9
        assert ev.round == 1
        assert ev.snippet.startswith("共用対象工事")
        # data/knowledge/F3_knowledge_関東電力.xlsx が実在するため導出される（D-11）
        assert ev.source_file == "F3_knowledge_関東電力.xlsx"

    def test_snippet_truncated(self, patch_stages, monkeypatch):
        """snippet は抜粋（全文貼付にしない・models.py docstring）"""
        long = _record(content="あ" * 1000)
        monkeypatch.setattr(pipeline, "load_f3", lambda **kw: [long])
        result = pipeline.ask("q", "関東電力")
        assert len(result.evidences[0].snippet) <= 200


class TestAbstainPaths:
    def test_zero_hits_shortcut(self, monkeypatch):
        """D-7: 検索0件は②を呼ばず即棄却"""
        monkeypatch.setattr(pipeline, "load_f3", lambda **kw: [])
        monkeypatch.setattr(
            pipeline, "check_sufficiency",
            lambda q, r: pytest.fail("検索0件で②が呼ばれた（D-7違反）"),
        )
        result = pipeline.ask("乾式キャスクの費用は？", "関東電力")
        assert result.status == "abstained"
        assert result.abstain_reason == "insufficient_context"
        assert result.related == []

    def test_insufficient(self, patch_stages, monkeypatch):
        """②が不十分と判定 → 棄却＋近傍ナレッジ（related）"""
        monkeypatch.setattr(
            pipeline, "check_sufficiency",
            lambda q, r: SufficiencyResult(sufficient=False, reason="答えが無い"),
        )
        result = pipeline.ask("q", "関東電力")
        assert result.abstain_reason == "insufficient_context"
        assert result.failed_stage is None
        assert len(result.related) > 0  # 起票時の参考（DESIGN §4-1）

    def test_low_grounding(self, patch_stages, monkeypatch):
        """④のスコアが閾値未満 → low_grounding で棄却（誤答より棄却）"""
        monkeypatch.setattr(
            pipeline, "check_grounding", lambda a, r: GroundingResult(score=0.1)
        )
        result = pipeline.ask("q", "関東電力")
        assert result.status == "abstained"
        assert result.abstain_reason == "low_grounding"
        assert result.answer is None  # ゲート不通過の回答は出さない


class TestGateErrors:
    """②③④の障害は gate_error で棄却に倒す（DESIGN §6）。failed_stage で分析可能に。"""

    def _boom(self, *args, **kwargs):
        raise RuntimeError("API failure")

    @pytest.mark.parametrize(
        "stage_attr, expected_stage",
        [
            ("check_sufficiency", "sufficiency"),
            ("generate_answer", "generation"),
            ("check_grounding", "grounding"),
        ],
    )
    def test_gate_failure_abstains(self, patch_stages, monkeypatch, stage_attr, expected_stage):
        monkeypatch.setattr(pipeline, stage_attr, self._boom)
        result = pipeline.ask("q", "関東電力")
        assert result.status == "abstained"
        assert result.abstain_reason == "gate_error"
        assert result.failed_stage == expected_stage

    def test_untagged_answer_abstains(self, patch_stages, monkeypatch):
        """有効な evidence タグの無い回答は出さない（REQUIREMENTS §0-4）"""
        monkeypatch.setattr(
            pipeline, "generate_answer",
            lambda q, r: GeneratedAnswer(answer="タグなし回答", cited_record_ids=[]),
        )
        result = pipeline.ask("q", "関東電力")
        assert result.abstain_reason == "gate_error"
        assert result.failed_stage == "generation"

    def test_inconsistent_usable_ids_abstains(self, patch_stages, monkeypatch):
        """②が sufficient なのに usable_record_ids が検索結果と不一致 → 棄却"""
        monkeypatch.setattr(
            pipeline, "check_sufficiency",
            lambda q, r: SufficiencyResult(
                sufficient=True, usable_record_ids=["存在しないID"], reason=""
            ),
        )
        result = pipeline.ask("q", "関東電力")
        assert result.abstain_reason == "gate_error"
        assert result.failed_stage == "sufficiency"


class TestSearchError:
    def test_search_failure_propagates(self, monkeypatch):
        """①の検索障害は棄却で吸収せず送出する（偽の棄却防止・DESIGN §6/D-14）"""
        def boom(**kwargs):
            raise KnowledgeSearchError("Vertex AI Search エラー")
        monkeypatch.setattr(pipeline, "load_f3", boom)
        with pytest.raises(KnowledgeSearchError):
            pipeline.ask("q", "関東電力")


class TestCaseContextExpansion:
    """D-19: 同一案件の往復メッセージ補完（small-to-big retrieval）。"""

    def test_missing_direction_is_completed(self, patch_stages, monkeypatch):
        """①が会話の片側しか返さなくても、補完で往復が②③に渡る（A-4型のヒット片側問題）"""
        first = _record(record_id="03_KT_1G_02_0001", seq=1, direction="nuro",
                        content="実績の人件費が計画比15%超過。超過理由と工数差異の説明を求める。")
        sibling = _record(record_id="03_KT_1G_02_0001", seq=2, direction="denryoku",
                          content="想定外の汚染拡大で除染工程が増加。差異説明書を提出します。")
        other = _record(record_id="03_KT_9G_9999", seq=1)  # IDクエリに混ざる他案件

        def fake_load_f3(**kw):
            # ①本体は片側のみ・補完（fee_type=案件ID）は往復＋他案件を返す
            if kw.get("fee_type") == "03_KT_1G_02_0001":
                return [first, sibling, other]
            return [first]
        monkeypatch.setattr(pipeline, "load_f3", fake_load_f3)

        seen: dict = {}
        def spy_sufficiency(q, records):
            seen["records"] = records
            return SufficiencyResult(
                sufficient=True, usable_record_ids=["03_KT_1G_02_0001"], reason=""
            )
        monkeypatch.setattr(pipeline, "check_sufficiency", spy_sufficiency)
        monkeypatch.setattr(
            pipeline, "generate_answer",
            lambda q, r: GeneratedAnswer(
                answer="超過理由と工数差異の説明（差異説明書）が必要です [F3#03_KT_1G_02_0001]",
                cited_record_ids=["03_KT_1G_02_0001"],
            ),
        )

        result = pipeline.ask("実績の人件費が計画を15%超過した場合の説明資料は？", "関東電力")
        # ②には往復が揃って渡り、他案件のメッセージは混入しない
        assert [r["_doc_id"] for r in seen["records"]] == [
            "03_KT_1G_02_0001_01", "03_KT_1G_02_0001_02",
        ]
        assert result.status == "answered"
        # 補完されたメッセージも引用根拠になる（往復の両方）
        assert [e.citation_key for e in result.evidences] == [
            ("03_KT_1G_02_0001", 1, "nuro"),
            ("03_KT_1G_02_0001", 1, "denryoku"),
        ]

    def test_dedupe_and_score_dropped(self, monkeypatch):
        """元ヒットは順位・スコア不変。補完分は末尾追加でIDクエリ由来スコアを落とす"""
        base = _record(seq=1, direction="nuro")
        sibling = _record(seq=2, direction="denryoku")
        monkeypatch.setattr(pipeline, "load_f3", lambda **kw: [base, sibling])
        out = pipeline._expand_case_context([base], "関東電力")
        assert [r["_doc_id"] for r in out] == [base["_doc_id"], sibling["_doc_id"]]
        assert out[0]["_rerank_score"] == 0.9   # 元ヒットのスコアは保持
        assert out[1]["_rerank_score"] is None  # 比較不能なスコアは表示しない

    def test_expansion_failure_continues(self, monkeypatch):
        """補完の検索失敗はベストエフォート＝本体の検索結果のみで続行（502にしない）"""
        base = _record()
        def boom(**kw):
            raise KnowledgeSearchError("Vertex AI Search エラー")
        monkeypatch.setattr(pipeline, "load_f3", boom)
        assert pipeline._expand_case_context([base], "関東電力") == [base]

    def test_disabled_by_config(self, patch_stages, monkeypatch):
        """INQUIRY_EXPAND_CASE_CONTEXT=0 相当で追加検索なし（前後比較・切り戻し用）"""
        monkeypatch.setattr(pipeline, "INQUIRY_EXPAND_CASE_CONTEXT", False)
        calls: list = []
        def counting(**kw):
            calls.append(kw)
            return [_record()]
        monkeypatch.setattr(pipeline, "load_f3", counting)
        pipeline.ask("q", "関東電力")
        assert len(calls) == 1  # ①本体の1回のみ

    def test_max_cases_cap(self, monkeypatch):
        """補完対象は検索上位 INQUIRY_EXPAND_MAX_CASES 案件まで（コール数の上限）"""
        records = [_record(record_id=f"03_KT_{i:04d}", seq=1) for i in range(5)]
        queried: list = []
        def counting(**kw):
            queried.append(kw.get("fee_type"))
            return []
        monkeypatch.setattr(pipeline, "load_f3", counting)
        monkeypatch.setattr(pipeline, "INQUIRY_EXPAND_MAX_CASES", 2)
        pipeline._expand_case_context(records, "関東電力")
        assert queried == ["03_KT_0000", "03_KT_0001"]
