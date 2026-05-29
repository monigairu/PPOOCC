# N対1 転記パイプライン Phase 2 実装まとめ

最終更新：2026-05-23（Phase 3 着手前の状態を反映）

---

## Phase 2 でやったこと

資料ファイルを読み込んで Gemini に渡せる `SourceDocument` に変換する Reader 層を実装した。

| 作業 | 内容 |
|---|---|
| STEP 3-a | `source_document.py`（SourceDocument・infer_document_kind・select_reader）を新規作成 |
| STEP 3-b | `excel_reader.py`（parser.py への薄いラッパ）を新規作成 |
| STEP 3-c | `word_reader.py`（parser.py への薄いラッパ）を新規作成 |
| STEP 3-d | `pdf_reader.py`（本当に新規。pypdf + Gemini multimodal フォールバック）を新規作成 |
| 依存追加 | `pypdf==6.12.1` を `uv add pypdf` で追加 |
| テスト | Reader モジュールのテストを 28 件作成・全件パス |

---

## ファイル構成（Phase 2 完了後）

```
nuro-ai-platform/
└── apps/backend/
    └── app/
        └── readers/                         ← 新規ディレクトリ
            ├── __init__.py
            ├── source_document.py           ← 新規
            ├── excel_reader.py              ← 新規（parser.py へのラッパ）
            ├── word_reader.py               ← 新規（parser.py へのラッパ）
            └── pdf_reader.py                ← 新規（pypdf + Gemini フォールバック）

apps/backend/tests/
    └── readers/                             ← 新規ディレクトリ
        ├── __init__.py
        ├── test_source_document.py          ← 新規（13件）
        ├── test_excel_reader.py             ← 新規（7件）
        └── test_pdf_reader.py               ← 新規（8件）
```

---

## データモデル

### SourceDocument

```python
@dataclass
class SourceDocument:
    source_file: str    # ファイル名（例: "見積書_A社.pdf"）
    source_type: str    # "excel" | "pdf" | "word"
    document_kind: str  # "見積書" | "物量データ" | "工程表" | "不明"
    text_content: str   # 全テキストをフラットダンプ（Gemini に渡す）
    metadata: dict      # ページ数・シート名などデバッグ用
```

### document_kind の推定ルール（infer_document_kind）

| document_kind | 判定キーワード |
|---|---|
| `見積書` | "見積", "estimate"（ファイル名を小文字化して比較）|
| `物量データ` | "物量", "quantity" |
| `工程表` | "工程", "schedule" |
| `不明` | 上記いずれにも該当しない |

> ファイル名は会社ごとに任意なので、この値は Gemini への抽出精度向上ヒントとして使うだけ。

---

## 各リーダーの実装

### excel_reader.py（parser.py への薄いラッパ）

Excel 読み込みロジックを二重実装しないよう、既存の `parser.py:parse_file()` を呼び出す。  
parser.py が全シートを行番号プレフィックス付きでフラットダンプするため、  
構造解析なしに Gemini が内容を読み取れる。

```python
def read_excel(file_path: str) -> SourceDocument:
    text_content = parse_file(file_path)       # parser.py に委譲
    wb = load_workbook(file_path, read_only=True, data_only=True)
    sheet_names = list(wb.sheetnames)
    wb.close()
    return SourceDocument(source_type="excel", ...)
```

### word_reader.py（parser.py への薄いラッパ）

同様に `parser.py:parse_file()` を呼び出す。  
parser.py が段落・表をフラットダンプする。

### pdf_reader.py（本当に新規）

```
pypdf でテキスト抽出
    ↓
抽出テキスト < 100 文字（スキャン PDF）?
    ├── No  → テキストをそのまま使用（[ページN] プレフィックス付き）
    └── Yes → _extract_via_gemini_multimodal() にフォールバック
                各ページを画像として Gemini 3.5 Flash に渡す
```

#### 安全弁

| 定数 | 値 | 動作 |
|---|---|---|
| `MAX_PAGES_PER_FILE` | 50 | 超過時は先頭 50 ページのみ処理し WARNING ログ |
| `_SCAN_PDF_THRESHOLD` | 100 文字 | 以下ならスキャン PDF と判定してフォールバック |

#### metadata の内容

```python
{
    "total_pages": 12,
    "processed_pages": 12,
    "used_multimodal_fallback": False,
}
```

---

## select_reader（ファイル振り分け）

```python
def select_reader(file_path: str) -> Callable:
    suffix = Path(file_path).suffix.lower()
    ".xlsx" / ".xls" → read_excel
    ".docx"          → read_word
    ".pdf"           → read_pdf
    other            → ValueError
```

---

## テスト結果

Phase 2 完了時点でテスト全体 **110 件中 108 件パス**（既存 2 件の失敗は変わらず）。

| テストファイル | 件数 | 主な確認内容 |
|---|---|---|
| `test_source_document.py` | 13 | infer_document_kind のキーワードマッチ・大文字小文字・select_reader の振り分け |
| `test_excel_reader.py` | 7 | SourceDocument のフィールド・シート名・セル値・複数シートのダンプ |
| `test_pdf_reader.py` | 8 | スキャン PDF 判定・フォールバック呼び出し・MAX_PAGES WARNING・上限内は WARNING なし |

---

## 実装上の決定事項

- **Word テストはなし**: `_parse_word` は既存 parser.py が担当し Phase 1 の時点で使われているため、Excel と同じパターンのラッパテストは省略した。
- **`PdfReader` はモジュールレベルインポート**: 関数内の遅延インポートでは `patch.object` が効かないため、ファイル先頭で `from pypdf import PdfReader` としてモック可能にした。
- **multimodal フォールバックは `patch.object` でモック**: Gemini API への実際の呼び出しは行わず、決定論的にテストする。

---

## データの流れ（Phase 2 完了後）

```
資料ファイル（N 件: .xlsx / .docx / .pdf）
    ↓
select_reader(filename) でリーダーを選択
    ↓
read_excel / read_word / read_pdf
    ├── Excel / Word → parser.py でテキスト変換
    └── PDF → pypdf でテキスト抽出（スキャンなら Gemini multimodal）
    ↓
SourceDocument
  source_file:    "見積書_A社.pdf"
  source_type:    "pdf"
  document_kind:  "見積書"
  text_content:   "[ページ1] 工事件名：○○配管解体工事\n御見積金額：143,500千円..."
  metadata:       {total_pages: 5, processed_pages: 5, used_multimodal_fallback: False}
    ↓
（Phase 3 へ）mapper.py 拡張版でフィールドと FormulaSpec を抽出
```

---

## Phase 3 着手前の準備事項

```
① mapper.py の現状確認
   map_to_schema() の引数・戻り値フォーマットを把握する
   SourceDocument.text_content を渡す際に既存の parse_file() 文字列と同じ形式かを確認

② call_gemini_structured の設計
   response_schema（JSON Schema）を使った structured output の API を確認する
   既存の call_gemini() とは別関数として追加し、既存テストへの影響をゼロにする

③ FormulaSpec の JSON スキーマ設計
   Gemini が formula_specs として返す JSON の構造を設計する
   safe_eval が受け取れる expression の形式（四則 + 許可関数）であることを Gemini に指示する
```
