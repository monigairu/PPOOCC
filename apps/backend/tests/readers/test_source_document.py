import pytest
from apps.backend.app.readers.source_document import infer_document_kind, select_reader


# ── infer_document_kind ──────────────────────────────────────────────────────

def test_infer_mitsumori():
    assert infer_document_kind("参考見積書_A社.pdf") == "見積書"

def test_infer_butsuryo():
    assert infer_document_kind("物量データ_2025.xlsx") == "物量データ"

def test_infer_koteihyo():
    assert infer_document_kind("工程表_改訂版.xlsx") == "工程表"

def test_infer_english_estimate():
    assert infer_document_kind("estimate_2025.pdf") == "見積書"

def test_infer_english_schedule():
    assert infer_document_kind("schedule_v2.xlsx") == "工程表"

def test_infer_unknown_filename():
    assert infer_document_kind("AA_20250401.xlsx") == "不明"

def test_infer_empty_stem():
    assert infer_document_kind(".xlsx") == "不明"

def test_infer_case_insensitive():
    # ファイル名は lower() して比較するため大文字混在でも判定できる
    assert infer_document_kind("ESTIMATE_final.pdf") == "見積書"


# ── select_reader ────────────────────────────────────────────────────────────

def test_select_reader_xlsx():
    from apps.backend.app.readers.excel_reader import read_excel
    assert select_reader("data.xlsx") is read_excel

def test_select_reader_xls():
    from apps.backend.app.readers.excel_reader import read_excel
    assert select_reader("data.xls") is read_excel

def test_select_reader_docx():
    from apps.backend.app.readers.word_reader import read_word
    assert select_reader("data.docx") is read_word

def test_select_reader_pdf():
    from apps.backend.app.readers.pdf_reader import read_pdf
    assert select_reader("data.pdf") is read_pdf

def test_select_reader_unsupported_raises():
    with pytest.raises(ValueError, match="未対応のファイル形式"):
        select_reader("data.csv")

def test_select_reader_case_insensitive():
    from apps.backend.app.readers.pdf_reader import read_pdf
    assert select_reader("data.PDF") is read_pdf
