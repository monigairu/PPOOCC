"""
pdf_reader のテスト

Gemini 呼び出し（_extract_via_gemini_multimodal）はモックを使う。
pypdf 自体の動作確認と、スキャン PDF 判定ロジックに集中する。
"""
import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import apps.backend.app.readers.pdf_reader as pdf_reader_module


def _make_minimal_pdf(tmp_path: Path, filename: str = "scan.pdf") -> Path:
    """pypdf で読めるが extract_text が空になる最小 PDF"""
    minimal_pdf = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
190
%%EOF"""
    path = tmp_path / filename
    path.write_bytes(minimal_pdf)
    return path


def _mock_reader(pages: list) -> MagicMock:
    """PdfReader のモックを生成する"""
    mock = MagicMock()
    mock.pages = pages
    return mock


# ── 基本プロパティ ───────────────────────────────────────────────────────────

def test_read_pdf_source_type(tmp_path):
    path = _make_minimal_pdf(tmp_path, "見積書_A社.pdf")
    with patch.object(pdf_reader_module, "_extract_via_gemini_multimodal", return_value="[ページ1] テスト"):
        doc = pdf_reader_module.read_pdf(str(path))
    assert doc.source_type == "pdf"


def test_read_pdf_document_kind_inferred(tmp_path):
    path = _make_minimal_pdf(tmp_path, "見積書_A社.pdf")
    with patch.object(pdf_reader_module, "_extract_via_gemini_multimodal", return_value="[ページ1] テスト"):
        doc = pdf_reader_module.read_pdf(str(path))
    assert doc.document_kind == "見積書"


def test_read_pdf_metadata_keys(tmp_path):
    path = _make_minimal_pdf(tmp_path)
    with patch.object(pdf_reader_module, "_extract_via_gemini_multimodal", return_value="[ページ1] テスト"):
        doc = pdf_reader_module.read_pdf(str(path))
    assert "total_pages" in doc.metadata
    assert "processed_pages" in doc.metadata
    assert "used_multimodal_fallback" in doc.metadata


# ── スキャン PDF 判定・フォールバック ─────────────────────────────────────────

def test_scan_pdf_triggers_multimodal_fallback(tmp_path):
    """テキスト抽出結果が閾値未満なら multimodal フォールバックが呼ばれる"""
    path = _make_minimal_pdf(tmp_path)
    mock_text = "[ページ1] Gemini が抽出したテキスト"

    with patch.object(pdf_reader_module, "_extract_via_gemini_multimodal", return_value=mock_text) as mock_fn:
        doc = pdf_reader_module.read_pdf(str(path))

    mock_fn.assert_called_once()
    assert doc.metadata["used_multimodal_fallback"] is True
    assert mock_text in doc.text_content


def test_text_pdf_does_not_trigger_fallback(tmp_path):
    """テキストが十分に抽出できた場合はフォールバックしない"""
    path = _make_minimal_pdf(tmp_path)

    # PdfReader を差し替えてテキストが十分に返る状態を作る
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "a" * 200  # 閾値 100 を超える
    mock_page.images = []

    with patch.object(pdf_reader_module, "PdfReader", return_value=_mock_reader([mock_page])):
        with patch.object(pdf_reader_module, "_extract_via_gemini_multimodal") as mock_fn:
            doc = pdf_reader_module.read_pdf(str(path))

    mock_fn.assert_not_called()
    assert doc.metadata["used_multimodal_fallback"] is False


# ── MAX_PAGES_PER_FILE 安全弁 ─────────────────────────────────────────────────

def test_max_pages_warning_logged(tmp_path, caplog):
    """50 ページ超のファイルは WARNING ログが出て先頭 50 ページのみ処理する"""
    path = _make_minimal_pdf(tmp_path, "large.pdf")

    mock_pages = [MagicMock() for _ in range(60)]
    for p in mock_pages:
        p.extract_text.return_value = "a" * 200
        p.images = []

    with patch.object(pdf_reader_module, "PdfReader", return_value=_mock_reader(mock_pages)):
        with caplog.at_level(logging.WARNING, logger=pdf_reader_module.__name__):
            doc = pdf_reader_module.read_pdf(str(path))

    assert doc.metadata["processed_pages"] == 50
    assert any("50" in r.message for r in caplog.records)


def test_max_pages_within_limit_no_warning(tmp_path, caplog):
    """50 ページ以内なら WARNING ログが出ない"""
    path = _make_minimal_pdf(tmp_path)

    mock_pages = [MagicMock() for _ in range(3)]
    for p in mock_pages:
        p.extract_text.return_value = "a" * 200
        p.images = []

    with patch.object(pdf_reader_module, "PdfReader", return_value=_mock_reader(mock_pages)):
        with caplog.at_level(logging.WARNING, logger=pdf_reader_module.__name__):
            doc = pdf_reader_module.read_pdf(str(path))

    assert doc.metadata["processed_pages"] == 3
    warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_msgs) == 0
