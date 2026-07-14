"""④ 接地検査の入力整形テスト（D-20）。

Check Grounding API 呼び出し自体は実測（eval_inquiry.py・プローブ）が担い、
ここでは API に渡す前の純関数（主張分解が機能する形への正規化・
案件単位の fact 結合）を検証する。
"""
from apps.backend.app.inquiry import grounding


def _msg(record_id, seq, direction, content):
    return {
        "id": record_id,
        "_doc_id": f"{record_id}_{seq:02d}",
        "message_direction": direction,
        "message_content": content,
    }


class TestNormalizeForClaims:
    def test_tag_removed_and_sentence_boundary_inserted(self):
        """タグ除去＋「。」直後の改行補完（複合主張化＝score≒0 の防止・D-20）"""
        answer = "差異説明書が必要です [F3#03_KT_1G_02_0001]。除染工程の増加が例です [F3#03_KT_1G_02_0001]。"
        normalized = grounding._normalize_for_claims(answer)
        assert "[F3#" not in normalized
        assert "。\n" in normalized  # 文境界に改行が入り、文単位で主張分解される

    def test_already_separated_text_unchanged(self):
        """既に改行・空白で区切られた文には手を入れない"""
        answer = "Aが必要です。\nBも必要です。"
        assert grounding._normalize_for_claims(answer) == answer


class TestBuildCaseFacts:
    def test_same_case_messages_merged_in_message_order(self):
        """同一案件の往復は1 fact に時系列（_doc_id順）で結合される"""
        records = [
            _msg("03_KT_1G_02_0001", 2, "denryoku", "差異説明書を提出します。"),
            _msg("03_KT_1G_02_0001", 1, "nuro", "超過理由と工数差異の説明を求める。"),
        ]
        facts = grounding._build_case_facts(records)
        assert len(facts) == 1
        assert facts[0].fact_text == (
            "【NuRO確認】超過理由と工数差異の説明を求める。\n"
            "【電力回答】差異説明書を提出します。"
        )
        assert facts[0].attributes["record_id"] == "03_KT_1G_02_0001"

    def test_different_cases_stay_separate(self):
        """別案件は fact を分ける（無関係な案件同士を1 fact に混ぜない）"""
        records = [
            _msg("03_KT_1G_02_0001", 1, "nuro", "a"),
            _msg("03_KT_1G_03_0003", 1, "nuro", "b"),
        ]
        facts = grounding._build_case_facts(records)
        assert [f.attributes["record_id"] for f in facts] == [
            "03_KT_1G_02_0001", "03_KT_1G_03_0003",
        ]

    def test_unknown_direction_shown_as_is(self):
        """未知の message_direction はそのまま表示（検査を止めない・D-11）"""
        facts = grounding._build_case_facts([_msg("x", 1, "other", "c")])
        assert facts[0].fact_text == "【other】c"
