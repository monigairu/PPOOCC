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

    def test_leading_connective_removed(self):
        """文頭の接続詞は検査入力から除去する（含意判定を壊す実測・D-21）"""
        answer = "区分一覧が必要です [F3#03_KT_2G_0004]。\n\nまた、内訳突合表の添付も求められます [F3#03_KT_2G_0001]。"
        normalized = grounding._normalize_for_claims(answer)
        assert "また、" not in normalized
        assert "内訳突合表の添付も求められます" in normalized

    def test_connective_after_inserted_boundary_removed(self):
        """改行補完で行頭に出た接続詞も除去される（補完→除去の順序）"""
        answer = "Aが必要です。さらに、Bも必要です。"
        normalized = grounding._normalize_for_claims(answer)
        assert normalized == "Aが必要です。\nBも必要です。"

    def test_connective_mid_sentence_kept(self):
        """文中の接続詞は主張の一部なので除去しない"""
        answer = "Aに加えて、Bも必要です。"
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

    def test_case_meta_line_prepended(self):
        """案件メタ行（費目・工事名等）を fact 先頭に置く（質問の話題語の支持・D-22）"""
        record = _msg("03_KT_1G_01_0010", 1, "nuro", "点検周期の根拠を明記されたい。")
        record.update(
            cost_category="維持管理費",
            construction_name="1号機換気空調設備定期点検",
            submission_timing="計画申請時",
            plant_site="館山発電所",
            plant_unit="1号機",
        )
        facts = grounding._build_case_facts([record])
        assert facts[0].fact_text == (
            "案件: 費目=維持管理費・工事名=1号機換気空調設備定期点検・"
            "提出タイミング=計画申請時・対象=館山発電所1号機\n"
            "【NuRO確認】点検周期の根拠を明記されたい。"
        )

    def test_case_meta_skips_empty_and_dash_values(self):
        """空・「－」の属性（事務連絡等）はメタ行に含めない"""
        record = _msg("03_KT_1G_01_0012", 1, "nuro", "命名規則に従い再提出されたい。")
        record.update(cost_category="－", construction_name="様式提出に関する事務連絡",
                      submission_timing="計画申請時", plant_site="館山発電所", plant_unit="－")
        facts = grounding._build_case_facts([record])
        assert facts[0].fact_text.startswith(
            "案件: 工事名=様式提出に関する事務連絡・提出タイミング=計画申請時・対象=館山発電所\n"
        )

    def test_no_meta_fields_no_meta_line(self):
        """案件属性が無いレコードは従来どおり本文のみ（後方互換）"""
        facts = grounding._build_case_facts([_msg("x", 1, "nuro", "c")])
        assert facts[0].fact_text == "【NuRO確認】c"
