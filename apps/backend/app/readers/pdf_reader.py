"""
PDF リーダー（本当に新規）

pypdf でテキスト抽出を試みる。
スキャン PDF（テキストが少ない）の場合は Gemini multimodal にフォールバックする。
"""
import logging
from pathlib import Path

from pypdf import PdfReader

from .source_document import SourceDocument, infer_document_kind

logger = logging.getLogger(__name__)

MAX_PAGES_PER_FILE = 50  # 1ファイルあたりの最大処理ページ数
_SCAN_PDF_THRESHOLD = 100  # テキストがこの文字数以下ならスキャン PDF と判定


def read_pdf(file_path: str) -> SourceDocument:
    """
    PDF ファイルを読み込んで SourceDocument を返す。

    テキスト PDF はテキスト抽出、スキャン PDF は Gemini multimodal にフォールバック。
    """
    reader = PdfReader(file_path)
    total_pages = len(reader.pages)

    if total_pages > MAX_PAGES_PER_FILE:
        logger.warning(
            f"[pdf_reader] {Path(file_path).name} は {total_pages} ページあり"
            f"上限 {MAX_PAGES_PER_FILE} を超えています。"
            f"先頭 {MAX_PAGES_PER_FILE} ページのみ処理します。"
        )

    pages_to_process = reader.pages[:MAX_PAGES_PER_FILE]

    text = "\n".join(
        f"[ページ{i + 1}] {page.extract_text() or ''}"
        for i, page in enumerate(pages_to_process)
    )

    used_fallback = False
    if len(text.strip()) < _SCAN_PDF_THRESHOLD:
        logger.info(
            f"[pdf_reader] {Path(file_path).name} はテキスト抽出結果が少ないため"
            " Gemini multimodal にフォールバックします。"
        )
        text = _extract_via_gemini_multimodal(file_path, list(pages_to_process))
        used_fallback = True

    return SourceDocument(
        source_file=file_path,
        source_type="pdf",
        document_kind=infer_document_kind(file_path),
        text_content=text,
        metadata={
            "total_pages": total_pages,
            "processed_pages": len(pages_to_process),
            "used_multimodal_fallback": used_fallback,
        },
    )


def _extract_via_gemini_multimodal(file_path: str, pages: list) -> str:
    """
    スキャン PDF の各ページを画像として Gemini に渡してテキストを抽出する。

    pypdf の PageObject から画像バイト列を取得し、Gemini multimodal API に送信する。
    """
    import base64
    from apps.backend.app.core.ai_client import _get_client
    from google.genai import types

    client = _get_client()
    lines: list[str] = []

    for i, page in enumerate(pages):
        page_num = i + 1

        # ページを画像としてレンダリングする（pypdf の extract_images を利用）
        images = list(page.images)
        if not images:
            lines.append(f"[ページ{page_num}] （画像なし）")
            continue

        # 最初の画像をページ代表として使用
        img_data = images[0].data
        img_b64 = base64.b64encode(img_data).decode()

        prompt_parts = [
            types.Part.from_bytes(
                data=img_data,
                mime_type="image/png",
            ),
            f"これは工事関連書類の {page_num} ページ目です。"
            "記載されているテキストをすべて抽出してください。"
            "表がある場合はタブ区切りで出力してください。",
        ]

        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt_parts,
                config=types.GenerateContentConfig(temperature=0.0),
            )
            page_text = response.text.strip()
        except Exception as e:
            logger.warning(f"[pdf_reader] ページ{page_num} の multimodal 抽出に失敗: {e}")
            page_text = ""

        lines.append(f"[ページ{page_num}] {page_text}")

    return "\n".join(lines)
