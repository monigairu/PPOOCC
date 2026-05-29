"""
Reader 層の共通データクラスとファイル振り分けロジック
"""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SourceDocument:
    source_file: str    # ファイル名（例: "見積書_A社.pdf"）
    source_type: str    # "excel" | "pdf" | "word"
    document_kind: str  # "見積書" | "物量データ" | "工程表" | "不明"
    text_content: str   # 全テキストをフラットダンプしたもの（Gemini に渡す）
    metadata: dict = field(default_factory=dict)  # ページ数・シート名などデバッグ用


_KIND_KEYWORDS: dict[str, list[str]] = {
    "見積書":    ["見積", "estimate"],
    "物量データ": ["物量", "quantity"],
    "工程表":    ["工程", "schedule"],
}


def infer_document_kind(file_path: str) -> str:
    """
    ファイル名から資料の種類を推定する（弱いヒント）。

    会社ごとにファイル名は任意なので、この値は Gemini への抽出精度向上ヒントとして
    使うだけ。分類に失敗しても "不明" として Gemini が本文から判断する。
    """
    name = Path(file_path).stem.lower()
    for kind, keywords in _KIND_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return kind
    return "不明"


def select_reader(file_path: str):
    """
    ファイルの拡張子から適切なリーダー関数を返す。

    Returns:
        Callable[[str], SourceDocument]
    """
    suffix = Path(file_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        from .excel_reader import read_excel
        return read_excel
    elif suffix == ".docx":
        from .word_reader import read_word
        return read_word
    elif suffix == ".pdf":
        from .pdf_reader import read_pdf
        return read_pdf
    raise ValueError(f"未対応のファイル形式: {suffix}（対応: .xlsx .xls .docx .pdf）")
