"""③ 引用付き回答生成のタグパース検証（DESIGN §3-3）。

LLM 呼び出しは monkeypatch で差し替え、「cited_record_ids は本文タグから導出し、
入力レコードに実在する record_id のみ採用する」という契約を検証する
（models.py GeneratedAnswer docstring・引用のでっち上げ排除）。
"""
import pytest

from apps.backend.app.inquiry import generation
from apps.backend.app.inquiry.generation import EVIDENCE_TAG_PATTERN, generate_answer

RECORDS = [
    {"id": "03_KT_1G_01_0002", "sheet_name": "KNI_1G_01", "round": "1",
     "message_direction": "nuro", "message_content": "根拠が不明。"},
    {"id": "03_KT_2G_0004", "sheet_name": "KNI_2G", "round": "1",
     "message_direction": "denryoku", "message_content": "区分一覧を提出。"},
]


def _patch_llm(monkeypatch, answer_text: str):
    monkeypatch.setattr(generation, "call_gemini", lambda *a, **kw: answer_text)


class TestEvidenceTagParsing:
    def test_cited_ids_derived_from_tags(self, monkeypatch):
        _patch_llm(
            monkeypatch,
            "根拠が必要との記録があります [F3#03_KT_1G_01_0002]。"
            "区分一覧の記録もあります [F3#03_KT_2G_0004]。",
        )
        result = generate_answer("q", RECORDS)
        assert result.cited_record_ids == ["03_KT_1G_01_0002", "03_KT_2G_0004"]

    def test_duplicate_tags_deduped_order_preserved(self, monkeypatch):
        _patch_llm(
            monkeypatch,
            "[F3#03_KT_2G_0004] と [F3#03_KT_1G_01_0002] と [F3#03_KT_2G_0004]",
        )
        result = generate_answer("q", RECORDS)
        assert result.cited_record_ids == ["03_KT_2G_0004", "03_KT_1G_01_0002"]

    def test_unknown_ids_rejected(self, monkeypatch):
        """実在しない record_id のタグは引用に採用しない（でっち上げ排除）"""
        _patch_llm(
            monkeypatch,
            "記録があります [F3#03_KT_1G_01_0002]。捏造タグ [F3#99_XX_0001]。",
        )
        result = generate_answer("q", RECORDS)
        assert result.cited_record_ids == ["03_KT_1G_01_0002"]

    def test_no_tags_returns_empty(self, monkeypatch):
        """タグ無し回答は cited が空 → pipeline 側で棄却される（gate_error）"""
        _patch_llm(monkeypatch, "タグの無い回答文。")
        result = generate_answer("q", RECORDS)
        assert result.cited_record_ids == []


class TestTagPattern:
    def test_pattern_matches_evidence_notation(self):
        """事前レビューと統一の evidence 記法（D-5）: [F3#record_id]"""
        assert EVIDENCE_TAG_PATTERN.findall(
            "回答 [F3#03_KT_1G_01_0002] と [F3#03_KT_2G_0004]"
        ) == ["03_KT_1G_01_0002", "03_KT_2G_0004"]

    def test_strip_for_grounding(self):
        """④に渡す前のタグ除去（grounding.py が使う）"""
        stripped = EVIDENCE_TAG_PATTERN.sub("", "本文A [F3#03_KT_1G_01_0002] 本文B")
        assert "F3#" not in stripped
