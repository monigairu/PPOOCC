"""問い合わせナレッジ対応自動化（inquiry）。

F3自社ナレッジへの引用付きRAG回答＋棄却時の起票管理（docs/inquiry/DESIGN.md）。
事前レビュー（preliminary_review/）とはシナリオ単位で分離し、改変しない。
ナレッジ検索は knowledge_loader.load_f3() を読み取りのみで再利用する（DESIGN D-1）。
"""
