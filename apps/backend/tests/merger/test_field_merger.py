"""
field_merger.py のテスト

優先順位マージ・競合検出・FIELD_SOURCE_OVERRIDE・解体機器リスト結合を確認する。
"""
import pytest

from apps.backend.app.merger.field_merger import (
    merge_extractions,
    normalize_equipment_list,
    FIELD_SOURCE_OVERRIDE,
    SOURCE_PRIORITY,
)


def _make_extraction(source_file: str, document_kind: str, data: dict, metadata: dict | None = None) -> dict:
    return {
        "source_file": source_file,
        "document_kind": document_kind,
        "data": data,
        "_metadata": metadata or {},
        "formula_specs": [],
    }


# ─── 単一ソース ───────────────────────────────────────────────────────────────

def test_single_source_no_conflict():
    ext = _make_extraction("見積書.pdf", "見積書", {"工事件名": "○○配管解体工事"})
    merged, conflicts = merge_extractions([ext])

    assert merged["工事件名"]["value"] == "○○配管解体工事"
    assert merged["工事件名"]["source_file"] == "見積書.pdf"
    assert conflicts == []


def test_single_source_none_values_excluded():
    ext = _make_extraction("見積書.pdf", "見積書", {"工事件名": "○○配管解体工事", "対象費目1": None})
    merged, conflicts = merge_extractions([ext])

    assert "工事件名" in merged
    assert "対象費目1" not in merged


# ─── 優先順位マージ ────────────────────────────────────────────────────────────

def test_priority_estimate_beats_quantity():
    """見積書（優先1）が物量データ（優先3）に勝つ"""
    exts = [
        _make_extraction("物量データ.xlsx", "物量データ", {"工事件名": "○○解体（物量版）"}),
        _make_extraction("見積書.pdf",      "見積書",    {"工事件名": "○○配管解体工事"}),
    ]
    merged, conflicts = merge_extractions(exts)

    assert merged["工事件名"]["value"] == "○○配管解体工事"
    assert merged["工事件名"]["source_file"] == "見積書.pdf"
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "工事件名"


def test_same_value_no_conflict():
    """複数ソースで値が一致する場合は conflicts に積まない"""
    exts = [
        _make_extraction("見積書.pdf",      "見積書",    {"工事件名": "○○配管解体工事"}),
        _make_extraction("物量データ.xlsx", "物量データ", {"工事件名": "○○配管解体工事"}),
    ]
    merged, conflicts = merge_extractions(exts)

    assert merged["工事件名"]["value"] == "○○配管解体工事"
    assert conflicts == []


# ─── FIELD_SOURCE_OVERRIDE ────────────────────────────────────────────────────

def test_field_source_override_for_schedule_date():
    """工期開始日は工程表を優先（見積書より優先度が低くても）"""
    exts = [
        _make_extraction("見積書.pdf",  "見積書", {"工期開始日": "2025年1月"}),
        _make_extraction("工程表.xlsx", "工程表", {"工期開始日": "2025年4月"}),
    ]
    merged, conflicts = merge_extractions(exts)

    assert merged["工期開始日"]["value"] == "2025年4月"
    assert merged["工期開始日"]["source_file"] == "工程表.xlsx"


def test_field_source_override_total_amount():
    """総額は見積書を優先"""
    exts = [
        _make_extraction("物量データ.xlsx", "物量データ", {"総額": 100_000_000}),
        _make_extraction("見積書.pdf",      "見積書",    {"総額": 143_500_000}),
    ]
    merged, conflicts = merge_extractions(exts)

    assert merged["総額"]["value"] == 143_500_000
    assert merged["総額"]["source_file"] == "見積書.pdf"


# ─── conflicts の内容検証 ─────────────────────────────────────────────────────

def test_conflicts_contain_all_candidates():
    """conflicts には全候補が含まれること"""
    exts = [
        _make_extraction("見積書.pdf",      "見積書",    {"工事件名": "A工事"}),
        _make_extraction("物量データ.xlsx", "物量データ", {"工事件名": "B工事"}),
    ]
    merged, conflicts = merge_extractions(exts)

    assert len(conflicts) == 1
    cands = conflicts[0]["candidates"]
    values = [c["value"] for c in cands]
    assert "A工事" in values
    assert "B工事" in values


# ─── metadata 伝搬 ────────────────────────────────────────────────────────────

def test_source_location_propagated():
    loc = {"file": "見積書.pdf", "page": 1}
    ext = _make_extraction(
        "見積書.pdf", "見積書",
        {"工事件名": "○○工事"},
        {"工事件名": {"confidence": "high", "source_location": loc}},
    )
    merged, _ = merge_extractions([ext])

    assert merged["工事件名"]["source_location"] == loc
    assert merged["工事件名"]["confidence"] == "high"


# ─── 解体機器リスト ───────────────────────────────────────────────────────────

def test_normalize_equipment_list_combines():
    list_a = [{"解体機器": "配管（50A）", "重量": 1.5}]
    list_b = [{"解体機器": "バルブ", "重量": 0.3}]
    result = normalize_equipment_list([list_a, list_b])

    assert len(result) == 2
    names = [r["解体機器"] for r in result]
    assert "配管（50A）" in names
    assert "バルブ" in names


def test_equipment_list_merged_from_multiple_sources():
    exts = [
        _make_extraction("物量データ.xlsx", "物量データ", {
            "解体機器リスト": [{"解体機器": "配管（50A）"}],
        }),
        _make_extraction("見積書.pdf", "見積書", {
            "解体機器リスト": [{"解体機器": "バルブ"}],
        }),
    ]
    merged, _ = merge_extractions(exts)

    equip = merged["解体機器リスト"]["value"]
    assert len(equip) == 2


def test_empty_list_excluded():
    """空リストは候補から除外する"""
    ext = _make_extraction("見積書.pdf", "見積書", {"解体機器リスト": []})
    merged, _ = merge_extractions([ext])
    assert "解体機器リスト" not in merged


# ─── エッジケース ─────────────────────────────────────────────────────────────

def test_unknown_document_kind_uses_lowest_priority():
    exts = [
        _make_extraction("不明資料.xlsx", "不明", {"工事件名": "不明版"}),
        _make_extraction("工程表.xlsx",   "工程表", {"工事件名": "工程表版"}),
    ]
    merged, _ = merge_extractions(exts)
    assert merged["工事件名"]["source_file"] == "工程表.xlsx"


def test_empty_extractions():
    merged, conflicts = merge_extractions([])
    assert merged == {}
    assert conflicts == []
