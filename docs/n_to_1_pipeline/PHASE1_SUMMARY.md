# N対1 転記パイプライン Phase 1 実装まとめ

最終更新：2026-05-23（Phase 2 着手前の状態を反映）

---

## Phase 1 でやったこと

LLM を使わない決定論的な基盤層を整備した。  
Phase 2 以降で LLM 呼び出しを追加しても、計算・単位変換・セル書き込み判定が正しく動くことをここで担保する。

| 作業 | 内容 |
|---|---|
| STEP 1 | `frames/frameB/MRC1.yaml` に `writable` フラグを追加 |
| STEP 2 | `formula_executor.py`（汎用計算検証）を新規作成 |
| STEP 2 | `unit_converter.py`（単位変換・純粋関数）を新規作成 |
| テスト | 上記 2 モジュールのテストを合計 40 件作成・全件パス |

---

## ファイル構成（Phase 1 時点）

```
nuro-ai-platform/
├── frames/
│   └── frameB/
│       └── MRC1.yaml                    ← writable フラグを追加
│
├── apps/backend/
│   └── app/
│       ├── tools/                       ← 新規ディレクトリ
│       │   ├── __init__.py
│       │   └── formula_executor.py      ← 新規
│       └── core/
│           └── unit_converter.py        ← 新規
│
└── apps/backend/tests/
    └── tools/                           ← 新規ディレクトリ
        ├── __init__.py
        ├── test_formula_executor.py     ← 新規（22件）
        └── test_unit_converter.py       ← 新規（18件）
```

---

## STEP 1: writable フラグ（MRC1.yaml）

`frames/frameB/MRC1.yaml` の `extraction_schema` に `writable` キーを追加した。

### 設計方針

- デフォルト `true`（未設定フィールドは書き込み対象）
- Excel 数式セルは `false`（書き込みをスキップ）
- **N 列全体・合計行（row 29）は extraction_schema で表現できないレイアウト保護**  
  → writer / tabular 側で「定義列・定義行の外には書かない」ことで保護

### 確定した writable: false

| フィールド | セル（plan / actual） | 理由 |
|---|---|---|
| `総額` | G18 / K18 | 他シート参照の数式（=SUM('MRC2'!...)） |
| `全体支払い対象金額` | G19 / K19 | 同上（TODO: 要確認） |

### 注意点

`plan_actual` タイプのフィールドは 1 フィールドが plan / actual の 2 セルに対応する。  
`writable: false` はフィールド単位の粗い保護。両セルが数式の場合のみ `false` を付けること。

---

## STEP 2: formula_executor.py

### 設計の考え方

工事の種類・会社が変わるたびに計算式も変わる。  
歩掛のような特定の計算式をコードにハードコードするのではなく、  
**Gemini が資料から抽出した計算仕様を Python が安全に再評価して検証**する設計にした。

```
Gemini が資料から抽出
  → formula_name / expression / variables / gemini_result
      ↓
  Python が safe_eval で再計算
      ↓
  相対誤差 ≦ 1%  → is_consistent=True → 採用
  相対誤差 > 1%  → needs_review=True  → conflicts に積んで人間確認
```

### 重要な設計上の前提

この相互チェックは「Gemini の算術ミス」を検出するものであり、  
**「Gemini の読み取りミス（係数の誤抽出）」は検出しない**。  
Gemini が間違った係数を抽出しても Python は同じ値を再計算して `is_consistent=True` になる。  
`FormulaResult.source_location` は必ず人間がレビューできる導線に含めること。

### データモデル

```python
@dataclass
class FormulaSpec:
    formula_name: str            # 例: "配管工数"
    expression: str              # 例: "ceil(weight * manhour_per_ton)"
    variables: dict[str, float]  # 例: {"weight": 1.5, "manhour_per_ton": 2.78}
    gemini_result: float         # Gemini が申告した計算結果
    result_unit: str             # 例: "人日", "円"
    source_location: dict        # どの資料のどの箇所から抽出したか

@dataclass
class FormulaResult:
    formula_name: str
    python_result: float         # Python による再計算値
    gemini_result: float         # Gemini の申告値
    is_consistent: bool          # 両者が一致するか（相対誤差 ≦ tolerance）
    result_unit: str
    needs_review: bool           # 不一致 → 人間確認が必要
    discrepancy_note: str | None # 不一致の場合の説明
    source_location: dict        # 抽出元（レビュー導線用）
```

### safe_eval の許可リスト

| カテゴリ | 許可内容 | 禁止内容 |
|---|---|---|
| 演算子 | `+ - * / ** -`（単項） | ビット演算・比較演算 |
| 関数 | `round ceil floor min max` | `abs` を含む上記以外のすべて |
| 構文 | 定数・変数参照・二項演算・単項演算・関数呼び出し | メソッド呼び出し・リスト内包・import など |

`__import__('os').system('ls')` → `ast.Call` の func が `ast.Name` でないため即エラー

### 主な関数

```python
def safe_eval(expression: str, variables: dict[str, float]) -> float
    """AST を手動ウォークして式を安全に評価する"""

def execute_formula(spec: FormulaSpec, tolerance: float = 1e-2) -> FormulaResult
    """FormulaSpec を再計算し Gemini 申告値と照合する"""
```

---

## STEP 2: unit_converter.py

### 設計方針

パイプライン全体（抽出 → マージ → 計算）を通じて金額は **円（元の単位）** で統一する。  
MRC1 への書き込み直前にのみここで変換する。

```
Gemini 抽出（円）→ マージ（円）→ formula_executor（円）→ unit_converter → Excel（千円）
```

### validator.py との関係（二重変換なし）

`validator.py` の `_validate_number()` は数値を **文字列のまま** 返す（元の形式を保持）。  
単位の数値変換は一切行わない。  
よって `unit_converter.py` が単一の変換ポイントになる。

### データの流れ

```python
# validator.py が返す値（文字列）
"143,500,000"

# unit_converter.py で float へ正規化してから変換
parse_to_float("143,500,000")  → 143_500_000.0
convert_unit(143_500_000.0, from_unit="円", to_unit="千円")  → 143_500.0
```

### 主な関数

```python
def parse_to_float(value) -> float | None
    """カンマ付き文字列・円記号付き文字列・数値を float に変換する。変換不可なら None。"""

def convert_unit(value, from_unit: str, to_unit: str) -> float | None
    """書き込み直前の単位変換。None が返ったら呼び出し元が WARNING を出してスキップする。"""
```

### 変換テーブル

| to_unit | 除数 |
|---|---|
| `千円` | 1,000 |
| `万円` | 10,000 |

---

## テスト結果

Phase 1 完了時点でテスト全体 **82 件中 80 件パス**（2 件は Phase 1 開始前からの既存失敗）。

| テストファイル | 件数 | 内容 |
|---|---|---|
| `test_formula_executor.py` | 22 | safe_eval セキュリティ・許可関数・境界値・source_location 伝播 |
| `test_unit_converter.py` | 18 | カンマ文字列・円記号・None・異常値・境界値 |
| 既存テスト | 40 | 全件パス（変更なし） |

### 既存の 2 件の失敗について

`test_fresh_eyes_fixes.py` の 2 件はエラーメッセージの文字列が英語想定で書かれているが  
実装が日本語メッセージを返す実装になっていることによる不一致。  
Phase 1 の変更とは無関係で、Phase 1 開始前から存在していた。

---

## データの流れ（Phase 1 時点）

Phase 1 では単体モジュールのみ。他モジュールとの接続は Phase 2 以降で構築する。

```
（Phase 2 で追加予定）
資料ファイル → Reader → SourceDocument → Gemini 抽出（mapper.py 拡張）
                                              ↓
                                        FormulaSpec
                                              ↓
                              ┌─────────────────────────┐
                              │  formula_executor.py     │  ← Phase 1 完了
                              │  execute_formula(spec)   │
                              │  → FormulaResult         │
                              └─────────────────────────┘
                                              ↓
                                    needs_review=True → conflicts
                                    is_consistent=True → 採用
                                              ↓
                              ┌─────────────────────────┐
                              │  unit_converter.py       │  ← Phase 1 完了
                              │  convert_unit(円→千円)   │
                              └─────────────────────────┘
                                              ↓
                                       MRC1 に書き込み
```

---

## Phase 2 着手前の準備事項

```
① pypdf のインストール確認
   uv add pypdf
   （python-docx は既存コードで使用済みのため不要）

② Gemini multimodal API の動作確認
   スキャン PDF のフォールバックで Gemini に画像を渡す際の API 形式を確認する

③ 既存 parser.py の動作確認
   Excel/Word ラッパーは parser.py を呼ぶだけなので、
   parser.py の返り値フォーマット（行番号プレフィックス付きテキスト）を把握しておく
```

---

## 用語集

| 用語 | 説明 |
|---|---|
| FormulaSpec | Gemini が資料から抽出した計算仕様。式・変数・申告結果・抽出元を保持する |
| FormulaResult | formula_executor が返す検証結果。Python 再計算値と Gemini 申告値の比較を含む |
| safe_eval | AST を手動ウォークして許可された演算子・関数のみを評価する安全な数式評価器 |
| is_consistent | Python 再計算値と Gemini 申告値の相対誤差が tolerance（デフォルト 1%）以内かどうか |
| needs_review | 不一致・評価エラー時に `True` になる。呼び出し元が conflicts に積む |
| writable | extraction_schema の各フィールドに付けるフラグ。`false` なら書き込みをスキップ |
| unit_converter | 書き込み直前の単位変換モジュール。パイプライン内で唯一の変換ポイント |
| parse_to_float | カンマ・円記号などを除いて文字列を float に変換する。変換不可なら None |
