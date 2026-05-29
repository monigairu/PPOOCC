"""
map_to_schema_from_doc / infer_plan_actual のテスト

Gemini 呼び出し（call_gemini_structured / call_gemini）はモックを使う。
JSON スキーマ構造・単位・FormulaSpec の型・信頼度マッピングに集中する。
"""
import pytest
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

import apps.backend.app.agents.data_extractor.mapper as mapper_module
from apps.backend.app.agents.data_extractor.mapper import (
    map_to_schema_from_doc,
    infer_plan_actual,
    EXTRACTION_RESPONSE_SCHEMA,
    PLANNING_KEYWORDS,
    ACTUAL_KEYWORDS,
)
from apps.backend.app.readers.source_document import SourceDocument
from apps.backend.app.tools.formula_executor import FormulaSpec


# ── テスト用フィクスチャ ───────────────────────────────────────────────────────

@pytest.fixture
def sample_doc():
    return SourceDocument(
        source_file="参考見積書_A社.pdf",
        source_type="pdf",
        document_kind="見積書",
        text_content="[ページ1] 御見積書\n工事件名: ○○配管解体工事\n御見積金額: 143,500,000円\n工期: 2025年4月〜2025年9月",
        metadata={},
    )


def _make_gemini_response(extracted_fields: dict, formula_specs: list = None) -> dict:
    """call_gemini_structured のモック戻り値を組み立てる"""
    return {
        "extracted_fields": extracted_fields,
        "formula_specs": formula_specs or [],
    }


# ── extraction_response_schema の構造 ─────────────────────────────────────────

def test_response_schema_has_required_keys():
    assert "extracted_fields" in EXTRACTION_RESPONSE_SCHEMA["properties"]
    assert "formula_specs" in EXTRACTION_RESPONSE_SCHEMA["properties"]
    assert "extracted_fields" in EXTRACTION_RESPONSE_SCHEMA["required"]
    assert "formula_specs" in EXTRACTION_RESPONSE_SCHEMA["required"]


# ── map_to_schema_from_doc の基本動作 ─────────────────────────────────────────

def test_map_to_schema_from_doc_returns_expected_keys(sample_doc):
    mock_resp = _make_gemini_response({
        "工事件名": {"value": "○○配管解体工事", "confidence": "high", "source_context": "ページ1"},
        "総額": {"value": 143500000, "confidence": "high", "source_context": "ページ1"},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert "extracted_data" in result
    assert "field_metadata" in result
    assert "formula_specs" in result


def test_extracted_data_values_present(sample_doc):
    mock_resp = _make_gemini_response({
        "工事件名": {"value": "○○配管解体工事", "confidence": "high", "source_context": "ページ1"},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert result["extracted_data"]["工事件名"] == "○○配管解体工事"


def test_amount_returned_in_yen_not_senyen(sample_doc):
    """総額は円単位（143500000）で返すこと。千円（143500）に変換しないこと"""
    mock_resp = _make_gemini_response({
        "総額": {"value": 143500000, "confidence": "high", "source_context": "ページ1"},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    # 143500（千円変換済み）ではなく 143500000（円単位）であること
    assert result["extracted_data"]["総額"] == 143500000


def test_confidence_high_maps_to_09(sample_doc):
    mock_resp = _make_gemini_response({
        "工事件名": {"value": "test", "confidence": "high", "source_context": None},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert result["field_metadata"]["工事件名"]["confidence"] == pytest.approx(0.9)


def test_confidence_medium_maps_to_07(sample_doc):
    mock_resp = _make_gemini_response({
        "工事件名": {"value": "test", "confidence": "medium", "source_context": None},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert result["field_metadata"]["工事件名"]["confidence"] == pytest.approx(0.7)


def test_confidence_low_maps_to_03(sample_doc):
    mock_resp = _make_gemini_response({
        "工事件名": {"value": "不確か", "confidence": "low", "source_context": "推測"},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert result["field_metadata"]["工事件名"]["confidence"] == pytest.approx(0.3)


def test_missing_schema_fields_get_null_and_zero_confidence(sample_doc):
    """スキーマにあるが Gemini が返さなかったフィールドは null + confidence 0.0"""
    mock_resp = _make_gemini_response({})  # 何も返さない
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    # 全フィールドが揃っているか
    from apps.backend.app.agents.data_extractor.mapper import _load_extraction_schema
    schema = _load_extraction_schema("frameB", "MRC1")
    for field_name in schema:
        assert field_name in result["extracted_data"]
        assert result["extracted_data"][field_name] is None
        assert result["field_metadata"][field_name]["confidence"] == 0.0


def test_source_context_stored_in_field_metadata(sample_doc):
    mock_resp = _make_gemini_response({
        "工事件名": {"value": "test", "confidence": "high", "source_context": "ページ1 工事件名欄"},
    })
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert result["field_metadata"]["工事件名"]["source_location"] == "ページ1 工事件名欄"


# ── FormulaSpec の抽出 ────────────────────────────────────────────────────────

def test_formula_specs_empty_when_none_in_response(sample_doc):
    mock_resp = _make_gemini_response({}, formula_specs=[])
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    assert result["formula_specs"] == []


def test_formula_spec_converted_to_dataclass(sample_doc):
    mock_resp = _make_gemini_response(
        {},
        formula_specs=[{
            "formula_name": "配管工数",
            "expression": "ceil(weight * manhour_per_ton)",
            "variables": {"weight": 1.5, "manhour_per_ton": 2.78},
            "gemini_result": 5.0,
            "result_unit": "人日",
            "source_location": {"file": "物量データ.xlsx", "sheet": "配管", "row": 5},
        }],
    )
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    specs = result["formula_specs"]
    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, FormulaSpec)
    assert spec.formula_name == "配管工数"
    assert spec.expression == "ceil(weight * manhour_per_ton)"
    assert spec.variables == {"weight": 1.5, "manhour_per_ton": 2.78}
    assert spec.gemini_result == pytest.approx(5.0)
    assert spec.result_unit == "人日"
    assert spec.source_location["file"] == "物量データ.xlsx"


def test_formula_spec_variables_are_float(sample_doc):
    """variables の値が float になっていること"""
    mock_resp = _make_gemini_response(
        {},
        formula_specs=[{
            "formula_name": "test",
            "expression": "a * b",
            "variables": {"a": 3, "b": 4},   # int で返ってくるケース
            "gemini_result": 12.0,
            "result_unit": "円",
        }],
    )
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    spec = result["formula_specs"][0]
    assert isinstance(spec.variables["a"], float)
    assert isinstance(spec.variables["b"], float)


def test_malformed_formula_spec_is_skipped(sample_doc):
    """不正な FormulaSpec（必須キー欠如）はスキップして例外を出さない"""
    mock_resp = _make_gemini_response(
        {},
        formula_specs=[
            {"formula_name": "bad_spec"},  # expression / variables がない
            {
                "formula_name": "good_spec",
                "expression": "a * b",
                "variables": {"a": 1.0, "b": 2.0},
                "gemini_result": 2.0,
                "result_unit": "円",
            },
        ],
    )
    with patch.object(mapper_module, "call_gemini_structured", return_value=mock_resp):
        result = map_to_schema_from_doc(sample_doc, "MRC1")
    # bad_spec はスキップされ good_spec のみ残る
    assert len(result["formula_specs"]) == 1
    assert result["formula_specs"][0].formula_name == "good_spec"


# ── infer_plan_actual ─────────────────────────────────────────────────────────

def test_infer_plan_actual_keyword_keikaku():
    doc = SourceDocument(
        source_file="見積書.pdf", source_type="pdf", document_kind="見積書",
        text_content="これは参考見積書です。工事の計画段階の資料です。",
        metadata={},
    )
    assert infer_plan_actual(doc) == "計画"


def test_infer_plan_actual_keyword_jisseki():
    doc = SourceDocument(
        source_file="報告書.pdf", source_type="pdf", document_kind="不明",
        text_content="実績報告書。工事は完了報告として提出します。",
        metadata={},
    )
    assert infer_plan_actual(doc) == "実績"


def test_infer_plan_actual_fallback_to_gemini_when_ambiguous():
    """キーワードが同数 → Gemini にフォールバックすること"""
    doc = SourceDocument(
        source_file="不明資料.pdf", source_type="pdf", document_kind="不明",
        text_content="特にキーワードのない資料。",
        metadata={},
    )
    with patch.object(mapper_module, "call_gemini", return_value="計画") as mock_fn:
        result = infer_plan_actual(doc)
    mock_fn.assert_called_once()
    assert result == "計画"


def test_infer_plan_actual_gemini_says_jisseki():
    doc = SourceDocument(
        source_file="不明資料.pdf", source_type="pdf", document_kind="不明",
        text_content="特にキーワードのない資料。",
        metadata={},
    )
    with patch.object(mapper_module, "call_gemini", return_value="実績"):
        result = infer_plan_actual(doc)
    assert result == "実績"


def test_infer_plan_actual_gemini_exception_returns_fumei():
    """Gemini が例外を投げた場合は「不明」を返すこと"""
    doc = SourceDocument(
        source_file="不明資料.pdf", source_type="pdf", document_kind="不明",
        text_content="特にキーワードのない資料。",
        metadata={},
    )
    with patch.object(mapper_module, "call_gemini", side_effect=Exception("API error")):
        result = infer_plan_actual(doc)
    assert result == "不明"
