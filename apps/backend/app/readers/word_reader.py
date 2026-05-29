"""
Word リーダー

既存の parser.py:_parse_word を再利用して SourceDocument を構築する。
Word 読み込みロジックを二重実装しない。
"""
from apps.backend.app.agents.data_extractor.parser import parse_file
from .source_document import SourceDocument, infer_document_kind


def read_word(file_path: str) -> SourceDocument:
    """
    Word ファイル（.docx）を読み込んで SourceDocument を返す。

    テキスト変換は parser.py に委譲する（段落・表をフラットダンプ）。
    """
    text_content = parse_file(file_path)

    return SourceDocument(
        source_file=file_path,
        source_type="word",
        document_kind=infer_document_kind(file_path),
        text_content=text_content,
        metadata={},
    )
