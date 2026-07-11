"""問い合わせナレッジ（inquiry）モデルの契約テスト（DESIGN §3・§4）。

Step 1 の完了条件＝モデルの直列化確認（DESIGN §7 フェーズ1）。
DESIGN §4-1 の JSON 例（回答時/棄却時）がそのままバリデーションを通ることと、
status⇔フィールドの整合（矛盾状態を作れないこと）を保証する。
record_id・message_direction 等の値は load_f3() が実際に返す形式に合わせる
（実データ: record_id="03_KT_1G_01_0002" 形式・message_direction="nuro"/"denryoku"）。
"""
import pytest
from pydantic import ValidationError

from apps.backend.app.inquiry.models import (
    AnswerCreate,
    AskRequest,
    AskResult,
    Evidence,
    GeneratedAnswer,
    GroundingResult,
    Inquiry,
    InquiryAnswer,
    InquiryCreate,
    SufficiencyResult,
)


class TestAskRequest:
    def test_matches_design_json(self):
        """DESIGN §4-1 のリクエスト例 {question, utility} がそのまま通る"""
        req = AskRequest.model_validate(
            {"question": "〇〇の記載粒度はどこまで必要ですか", "utility": "関東電力"}
        )
        assert req.utility == "関東電力"

    def test_empty_fields_rejected(self):
        """空の質問・空の会社名は受け付けない（自社フィルタ必須・REQUIREMENTS §0-3）"""
        with pytest.raises(ValidationError):
            AskRequest(question="", utility="関東電力")
        with pytest.raises(ValidationError):
            AskRequest(question="q", utility="")


class TestAskResult:
    """`/api/inquiry/ask` レスポンス本体の契約"""

    def test_answered_matches_design_json(self):
        """DESIGN §4-1 の「回答時」JSON例（実データのID形式）がそのまま通る"""
        payload = {
            "status": "answered",
            "answer": "…（引用タグ付き回答文）…",
            "evidences": [
                {
                    "record_id": "03_KT_1G_01_0002",
                    "source_file": "F3_knowledge_関東電力.xlsx",
                    "sheet": "KNI_1G_01",
                    "snippet": "…該当箇所の抜粋…",
                    "score": 0.92,
                }
            ],
            "grounding_score": 0.87,
            "related": [],
            "abstain_reason": None,
        }
        result = AskResult.model_validate(payload)
        assert result.status == "answered"
        assert result.evidences[0].record_id == "03_KT_1G_01_0002"
        # ラウンドトリップ（Firestore保存＝dict化→復元）
        assert AskResult.model_validate(result.model_dump()) == result

    def test_abstained_matches_design_json(self):
        """DESIGN §4-1 の「棄却時」JSON例がそのまま通る"""
        payload = {
            "status": "abstained",
            "answer": None,
            "evidences": [],
            "grounding_score": None,
            "related": [],
            "abstain_reason": "insufficient_context",
        }
        result = AskResult.model_validate(payload)
        assert result.status == "abstained"
        assert result.abstain_reason == "insufficient_context"

    def test_status_is_closed_vocabulary(self):
        """棄却とエラーを混同しない（DESIGN §6）：status に 'error' は存在しない"""
        with pytest.raises(ValidationError):
            AskResult(status="error")

    def test_abstain_reason_vocabulary(self):
        """abstain_reason は3値のみ（insufficient_context/low_grounding/gate_error）"""
        for reason in ("insufficient_context", "low_grounding", "gate_error"):
            r = AskResult(status="abstained", abstain_reason=reason)
            assert r.abstain_reason == reason
        with pytest.raises(ValidationError):
            AskResult(status="abstained", abstain_reason="timeout")

    def test_status_field_consistency_enforced(self):
        """status とフィールドの矛盾状態を構築できない（バリデータで強制）"""
        # answered なのに answer が無い
        with pytest.raises(ValidationError):
            AskResult(status="answered")
        # answered なのに abstain_reason がある
        with pytest.raises(ValidationError):
            AskResult(status="answered", answer="回答", abstain_reason="low_grounding")
        # abstained なのに answer がある
        with pytest.raises(ValidationError):
            AskResult(status="abstained", answer="回答",
                      abstain_reason="insufficient_context")
        # abstained なのに abstain_reason が無い
        with pytest.raises(ValidationError):
            AskResult(status="abstained")

    def test_gate_error_carries_failed_stage(self):
        """gate_error 時にどのゲートで落ちたかを記録できる（評価・較正の分析用）"""
        r = AskResult(status="abstained", abstain_reason="gate_error",
                      failed_stage="grounding")
        assert r.failed_stage == "grounding"
        with pytest.raises(ValidationError):
            AskResult(status="abstained", abstain_reason="gate_error",
                      failed_stage="retrieval")  # ②③④以外は語彙外


class TestEvidence:
    def test_citation_unit_fields_with_real_vocabulary(self):
        """引用の最小単位＝record_id + round + message_direction（D-9）。
        message_direction は実データの語彙（'nuro'/'denryoku'）で保持する"""
        ev = Evidence(
            record_id="03_KT_1G_01_0002",
            sheet="KNI_1G_01",
            snippet="隣接する廃棄物処理工事と仮設足場・揚重機を共用し約800万円削減見込み。",
            round=1,
            message_direction="denryoku",
        )
        assert ev.citation_key == ("03_KT_1G_01_0002", 1, "denryoku")

    def test_source_file_optional(self):
        """source_file は任意（BQ平坦化テーブルに原本ファイル名の列が無いため）。
        score/round/direction も任意（無くても契約違反にしない）"""
        ev = Evidence(record_id="x", sheet="s", snippet="t")
        assert ev.source_file is None and ev.score is None
        assert ev.citation_key == ("x", None, None)  # None時は record_id 単位に落ちる


class TestPipelineStages:
    def test_sufficiency_result_roundtrip(self):
        r = SufficiencyResult(sufficient=False, reason="F3に該当レコードなし")
        assert r.usable_record_ids == []
        assert SufficiencyResult.model_validate(r.model_dump()) == r

    def test_generated_answer_roundtrip(self):
        g = GeneratedAnswer(answer="回答 [F3#03_KT_1G_01_0002]",
                            cited_record_ids=["03_KT_1G_01_0002"])
        assert GeneratedAnswer.model_validate(g.model_dump()) == g

    def test_grounding_result_roundtrip(self):
        g = GroundingResult(
            score=0.975,
            claim_citations=[{"claim_text": "約800万円の削減見込み", "citation_indices": [0]}],
        )
        assert GroundingResult.model_validate(g.model_dump()) == g


class TestInquiry:
    def test_create_with_self_solve_log(self):
        """起票時に棄却応答（AskResult）をそのままログとして抱き込める（§4-2）"""
        log = AskResult(status="abstained", abstain_reason="insufficient_context")
        req = InquiryCreate(category="質問", content="乾式キャスク保管費用は支払い対象か",
                            requester="関東電力", self_solve_log=log)
        assert req.self_solve_log.status == "abstained"

    def test_inquiry_document_roundtrip(self):
        """Firestore ドキュメント（DESIGN §4-2）の dict 往復"""
        doc = Inquiry(
            inquiry_id="abc123", number="0001", category="質問",
            content="…", requester="関東電力",
            created_at="2026-07-11T00:00:00Z", updated_at="2026-07-11T00:00:00Z",
        )
        assert doc.status == "open"  # 初期状態（§1-3）
        assert Inquiry.model_validate(doc.model_dump()) == doc

    def test_answered_inquiry_roundtrip(self):
        """回答済みドキュメント（AnswerCreate→InquiryAnswer→Inquiry.answer）の往復"""
        req = AnswerCreate(content="対象です。区分一覧を参照ください。",
                           answered_by="NuRO管理G")
        ans = InquiryAnswer(**req.model_dump(), answered_at="2026-07-11T01:00:00Z")
        doc = Inquiry(
            inquiry_id="abc124", number="0002", category="質問",
            content="…", requester="関東電力", status="answered",
            created_at="2026-07-11T00:00:00Z", updated_at="2026-07-11T01:00:00Z",
            answer=ans,
        )
        assert Inquiry.model_validate(doc.model_dump()) == doc
        assert doc.answer.answered_by == "NuRO管理G"

    def test_status_vocabulary(self):
        """ステータスは最小3状態のみ（REQUIREMENTS §3-2）"""
        with pytest.raises(ValidationError):
            Inquiry(
                inquiry_id="x", number="0003", category="質問", content="…",
                requester="関東電力", status="pending",
                created_at="2026-07-11T00:00:00Z", updated_at="2026-07-11T00:00:00Z",
            )
