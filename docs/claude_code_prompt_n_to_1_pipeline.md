# Claude Code タスク: N対1 転記パイプラインの実装

## まず最初にやること

```bash
# 既存コードとテストの状態を確認してから着手すること
find apps/backend -name "*.py" | sort
PYTHONPATH=. uv run pytest apps/backend/tests/ -v
git status
```

---

## プロジェクトコンテキスト

NuRO（使用済燃料再処理・廃炉推進機構）向け廃炉情報管理システム Step4 の PoC。
解体工事の建設関連書類（Excel/Word/PDF）から NuRO 様式（MRC1）へのデータ転記を自動化する。

**スタック**: Python 3.12 / FastAPI / Vertex AI（Gemini 3.5 Flash: `gemini-3.5-flash`）/ uv / openpyxl / pyyaml  
**既存パターン**:
- テスト: `apps/backend/tests/` に pytest
- 手動確認スクリプト: `scripts/check_*.py`
- 設定: YAML 外部化（`frames/frameB/MRC1.yaml` に extraction_schema + セル定義）
- 既存パイプライン: `app/agents/data_extractor/`（parser → mapper → validator 三層構造）

---

## 現状と変更目標

| | 現状 | 今回の目標 |
|---|---|---|
| 入力 | 単一ファイル（1資料） | 複数ファイル（N資料） |
| 出力 | MRC1 の各セルに転記 | 同左（N対1） |
| 計算 | なし | 汎用 formula_executor で計算式を資料から抽出・検証 |

**実際の入力資料の組み合わせ例**:
- `物量データ.xlsx` → 解体機器リスト（機器名・作業区域・口径・重量）
- `参考見積書.pdf` → 工事件名・総額・実施内容・費目
- `工程表.xlsx` → 工期開始日・工期終了日

---

## アーキテクチャ方針（絶対に守ること）

以下の役割分担は変更禁止。

| 役割 | 担当 | 禁止事項 |
|---|---|---|
| 資料からの値・計算仕様の抽出 | Gemini 3.5 Flash（structured output） | 算術計算を絶対にさせない |
| 計算の実行・検証 | 汎用 formula_executor（Python） | LLM に計算させない。特定の計算式をハードコードしない |
| 単位変換（円→千円など） | unit_converter.py（書き込み直前のみ） | 抽出・マージ中は元の単位（円）のまま扱う |
| セル番地の決定（固定部分） | YAML ルックアップ（決め打ち） | Gemini にセル番地を判断させない |
| MRC1 への書き込み | openpyxl（既存の form_generation_pipeline を拡張） | 数式セル（writable:false）には書かない |

**Gemini がやること**: テキスト・PDF・画像から構造化 JSON を抽出すること。計算仕様（式・変数・申告結果）の抽出も含む。  
**Gemini がやらないこと**: 四則演算の実行、単位変換、セル番地の判断、ファイル操作。

**計算の汎用設計原則**:  
会社・工事の種類によって計算式は毎回変わる。計算式をコードにハードコードするのではなく、Gemini が資料から計算式と変数値を抽出し、Python が安全に再評価して検証する。Gemini の申告値と Python の再計算値が一致すれば採用、不一致なら `conflicts` に積んで人間確認に回す。

> **【重要な設計上の前提】** formula_executor の相互チェックは「Gemini の算術ミス」を検出するものであり、「Gemini の読み取りミス（係数の誤抽出）」は検出しない。Gemini が間違った係数を抽出しても Python は同じ値を再計算して `is_consistent=True` になる。したがって FormulaResult の `source_location` は必ず人間がレビューできる導線（チャット・conflicts 表示）に含めること。

**単位の統一原則**:  
抽出・マージ・計算を通じて、金額はすべて **円（元の単位）** で扱う。`frames/frameB/MRC1.yaml` の `unit: 千円` は「書き込み時に千円に変換せよ」という writer への指示として使う。パイプライン中に中途半端に変換すると複数ソース間の比較・競合解決で桁ずれバグが起きる。

---

## 本当に新規なもの vs 既存資産の拡張（最重要）

コードベースを読むと、要件書が「新規」としているものの多くはすでに存在する。重複実装は E2E テスト（33件）を壊すリスクがある。

| 要件 STEP | 実態 | 対応方針 |
|---|---|---|
| STEP 3 Excel/Word 読み込み | `app/agents/data_extractor/parser.py` の `_parse_excel` / `_parse_word` が全シートフラットダンプを既に実装 | **PDF のみ新規追加**。Excel/Word は parser.py に相乗りして SourceDocument を構築する薄いラッパで対応 |
| STEP 4 Gemini 抽出 | `data_extractor/mapper.py`（LLM 抽出）+ `validator.py`（型変換・信頼度・source_location）が担当 | **拡張**（計算仕様抽出・structured output 追加） |
| STEP 6 MRC1 書き込み | `pipelines/form_generation_pipeline.py:generate_form_from_dict()` が dict→Excel（通常フィールド＋表形式＋source_location）を担当。`tabular_handler.write_tabular_section` / `cell_writer.write_to_cell` も既存 | **拡張**（writable フラグ・単位変換・max_rows 追加） |
| セル番地 YAML 決め打ち | `cell_locator_agent.determine_cell_mapping()` が YAML 優先・未定義のみ LLM フォールバック | **既存で要件を満たす** |
| **本当に新規なもの** | PDF リーダー・formula_executor・unit_converter・N:1 マージ・非同期 API | **新規実装** |

---

## 実装タスク（この順番で進めること）

### STEP 1: MRC1.yaml に writable フラグを追加

**対象ファイル**: `frames/frameB/MRC1.yaml`（`extraction_schema` セクション）  
※ `data/review_criteria/frameB_MRC1.yaml` は全くの別物（レビュー観点定義 RC001-RC008）なので混同しないこと。  
デフォルトは `true`。Excel 数式が入っているセルは `false`。

```yaml
# 追加例
総額:
  type: number
  required: true
  unit: 千円
  writable: false        # ← 追加: =SUM('MRC2'!...) の数式セル
  description: "..."

全体支払い対象金額:
  type: number
  required: false
  writable: false        # ← 追加: 同上（確認済みなら false、未確認なら true のまま TODO コメントを付ける）
```

**確定している writable:false の extraction_schema キー**:
- `総額`（plan: G18, actual: K18）: 他シート参照の数式
- `全体支払い対象金額`（plan: G19, actual: K19）: 同上（要確認）

**writable では表現できない保護（writer/tabular 側で対応）**:
- 差分列（N 列全体）: 実績 - 計画 の数式列 → write_tabular_section が定義列以外に書かないことで保護
- 解体機器表の合計行（row 29）: SUM 数式 → `data_start_row: 30` より前の行には書かないことで保護
- plan_actual フィールドは1フィールドが plan/actual 2セルに対応するため、`writable: false` はフィールド単位の粗い保護になる。現時点では両セルが数式の場合のみ `false` を付けること。

**ルール**: 不明なセルは `writable: true`（デフォルト）のまま保留し、`# TODO: 要確認` コメントを付けること。

---

### STEP 2: 汎用計算エグゼキューターの実装（新規・LLM 不使用）

`apps/backend/app/tools/formula_executor.py` を新規作成。

**設計の考え方**:  
工事の種類・会社が変わるたびに計算式も変わる。歩掛のような特定の計算式をコードにハードコードするのではなく、Gemini が資料から抽出した計算仕様を Python が安全に再評価して検証する設計にする。

```python
"""
汎用計算エグゼキューター
Gemini が資料から抽出した計算仕様を受け取り、Python で安全に再計算・検証する。
特定の計算式（歩掛など）はハードコードしない。

【設計上の注意】
このモジュールは Gemini の「算術ミス」を検出するが、
「係数や変数の読み取りミス」は検出しない。
FormulaResult.source_location を必ず人間レビューの導線に含めること。
"""
from dataclasses import dataclass
import ast
import math
import operator

@dataclass
class FormulaSpec:
    """Gemini が資料から抽出した計算仕様"""
    formula_name: str            # 例: "配管工数", "材料費"
    expression: str              # 例: "weight * manhour_per_ton"
    variables: dict[str, float]  # 例: {"weight": 1.5, "manhour_per_ton": 2.78}
    gemini_result: float         # Gemini が申告した計算結果
    result_unit: str             # 例: "人日", "円"
    source_location: dict        # どの資料のどの箇所から抽出したか

@dataclass
class FormulaResult:
    formula_name: str
    python_result: float         # Python による再計算値
    gemini_result: float         # Gemini の申告値
    is_consistent: bool          # 両者が一致するか
    result_unit: str
    needs_review: bool           # 不一致 → 人間確認が必要
    discrepancy_note: str | None # 不一致の場合の説明
    source_location: dict        # 抽出元（レビュー導線用）

# 許可する演算子（セキュリティ上、exec/eval は使わない）
_ALLOWED_OPERATORS = {
    ast.Add:  operator.add,
    ast.Sub:  operator.sub,
    ast.Mult: operator.mul,
    ast.Div:  operator.truediv,
    ast.Pow:  operator.pow,
    ast.USub: operator.neg,
}

# 許可する組み込み関数（四則+べき乗では表現できない歩掛計算に必要）
_ALLOWED_FUNCTIONS = {
    "round": round,
    "ceil":  math.ceil,
    "floor": math.floor,
    "min":   min,
    "max":   max,
}

def safe_eval(expression: str, variables: dict[str, float]) -> float:
    """
    四則演算・べき乗・許可関数のみを許可する安全な数式評価器。
    exec/eval は使わず、AST を手動でウォークする。

    例:
        safe_eval("ceil(weight * manhour_per_ton)", {"weight": 1.5, "manhour_per_ton": 2.78})
        → 5.0
    """
    tree = ast.parse(expression, mode='eval')
    return _eval_node(tree.body, variables)

def _eval_node(node, variables: dict) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ValueError(f"変数 '{node.id}' が variables に見つかりません: {list(variables.keys())}")
        return float(variables[node.id])
    if isinstance(node, ast.BinOp):
        op_func = _ALLOWED_OPERATORS.get(type(node.op))
        if not op_func:
            raise ValueError(f"許可されていない演算子: {type(node.op).__name__}")
        return op_func(_eval_node(node.left, variables), _eval_node(node.right, variables))
    if isinstance(node, ast.UnaryOp):
        op_func = _ALLOWED_OPERATORS.get(type(node.op))
        if not op_func:
            raise ValueError(f"許可されていない演算子: {type(node.op).__name__}")
        return op_func(_eval_node(node.operand, variables))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("メソッド呼び出しは許可されていません")
        func_name = node.func.id
        func = _ALLOWED_FUNCTIONS.get(func_name)
        if not func:
            raise ValueError(f"許可されていない関数: {func_name}。許可リスト: {list(_ALLOWED_FUNCTIONS)}")
        args = [_eval_node(arg, variables) for arg in node.args]
        return float(func(*args))
    raise ValueError(f"許可されていない構文: {type(node).__name__}")

def execute_formula(spec: FormulaSpec, tolerance: float = 1e-2) -> FormulaResult:
    """
    FormulaSpec を受け取り Python で再計算し、Gemini の申告値と照合する。

    不一致（tolerance 以上の相対誤差）の場合は needs_review=True にして
    呼び出し元が conflicts に積むこと。

    Args:
        spec: Gemini が抽出した計算仕様
        tolerance: 許容誤差（デフォルト: 1% = 0.01）

    Returns:
        FormulaResult（is_consistent=False の場合は人間確認が必要）
    """
    try:
        python_result = safe_eval(spec.expression, spec.variables)
    except (ValueError, ZeroDivisionError) as e:
        return FormulaResult(
            formula_name=spec.formula_name,
            python_result=float('nan'),
            gemini_result=spec.gemini_result,
            is_consistent=False,
            result_unit=spec.result_unit,
            needs_review=True,
            discrepancy_note=f"計算式の評価エラー: {e}",
            source_location=spec.source_location,
        )

    # 相対誤差で比較（ゼロ除算防止）
    if spec.gemini_result != 0:
        relative_error = abs(python_result - spec.gemini_result) / abs(spec.gemini_result)
        is_consistent = relative_error <= tolerance
    else:
        is_consistent = abs(python_result) <= tolerance

    return FormulaResult(
        formula_name=spec.formula_name,
        python_result=python_result,
        gemini_result=spec.gemini_result,
        is_consistent=is_consistent,
        result_unit=spec.result_unit,
        needs_review=not is_consistent,
        discrepancy_note=(
            f"Python={python_result:.4f} vs Gemini={spec.gemini_result:.4f}"
            if not is_consistent else None
        ),
        source_location=spec.source_location,
    )
```

**テスト**: `apps/backend/tests/tools/test_formula_executor.py`

```python
from apps.backend.app.tools.formula_executor import FormulaSpec, execute_formula, safe_eval

def test_safe_eval_basic_arithmetic():
    assert safe_eval("a * b", {"a": 2.0, "b": 3.0}) == 6.0

def test_safe_eval_allowed_functions():
    import math
    assert safe_eval("ceil(a)", {"a": 2.3}) == math.ceil(2.3)
    assert safe_eval("round(a)", {"a": 2.567}) == round(2.567)
    assert safe_eval("min(a, b)", {"a": 3.0, "b": 5.0}) == 3.0

def test_safe_eval_complex_expression():
    # 歩掛計算式のイメージ: 工数 = ceil(重量 * 基準工数/t)
    result = safe_eval(
        "ceil(weight * manhour_per_ton)",
        {"weight": 1.5, "manhour_per_ton": 2.78}
    )
    assert result > 0

def test_safe_eval_disallows_dangerous_code():
    import pytest
    with pytest.raises((ValueError, Exception)):
        safe_eval("__import__('os').system('ls')", {})

def test_safe_eval_disallows_method_calls():
    import pytest
    with pytest.raises(ValueError):
        safe_eval("a.evil()", {"a": 1.0})

def test_execute_formula_consistent():
    spec = FormulaSpec(
        formula_name="単純積",
        expression="a * b",
        variables={"a": 3.0, "b": 4.0},
        gemini_result=12.0,
        result_unit="人日",
        source_location={},
    )
    result = execute_formula(spec)
    assert result.is_consistent is True
    assert result.needs_review is False

def test_execute_formula_inconsistent_triggers_review():
    spec = FormulaSpec(
        formula_name="誤計算",
        expression="a * b",
        variables={"a": 3.0, "b": 4.0},
        gemini_result=99.0,   # Gemini が間違えた値を申告
        result_unit="人日",
        source_location={},
    )
    result = execute_formula(spec)
    assert result.is_consistent is False
    assert result.needs_review is True  # → conflicts に積まれる

def test_execute_formula_undefined_variable_raises():
    spec = FormulaSpec(
        formula_name="未定義変数",
        expression="a * undefined_var",
        variables={"a": 3.0},
        gemini_result=0.0,
        result_unit="円",
        source_location={},
    )
    result = execute_formula(spec)
    assert result.needs_review is True  # クラッシュせず review 扱いになること

def test_source_location_propagated_to_result():
    # source_location が FormulaResult に渡ること（人間レビュー導線の確認）
    loc = {"file": "歩掛計算シート.xlsx", "sheet": "配管基準工数", "row": 15}
    spec = FormulaSpec(
        formula_name="配管工数",
        expression="a * b",
        variables={"a": 1.0, "b": 2.0},
        gemini_result=2.0,
        result_unit="人日",
        source_location=loc,
    )
    result = execute_formula(spec)
    assert result.source_location == loc
```

---

### STEP 3: ソースリーダーの実装

**【重要】Excel / Word は既存 `parser.py` を再利用する。PDF のみ新規実装。**

`apps/backend/app/readers/` ディレクトリを作成し、以下を実装。

#### 共通の出力フォーマット

```python
@dataclass
class SourceDocument:
    source_file: str        # ファイル名（例: "見積書_A社.pdf"）
    source_type: str        # "excel" | "pdf" | "word"
    document_kind: str      # 資料の種類ヒント（例: "見積書" | "物量データ" | "工程表" | "不明"）
    text_content: str       # 全テキストをフラットにダンプしたもの（Gemini に渡す）
    metadata: dict          # ページ数、シート名リストなど（デバッグ用）
```

`document_kind` はファイル名の一部マッチで推定する（例: "見積" を含む → "見積書"）。
不明な場合は `"不明"` のまま Gemini に渡す。Gemini は document_kind をヒントとして使う。

> **【設計上の注意】** `document_kind` はファイル名からの弱いヒントに過ぎない。会社によってファイル名は任意（"AA_20250401.xlsx" のような無名義ファイルもある）。ファイル名でロジックを分岐させず、あくまで Gemini への抽出精度向上用のヒントとして使うこと。分類の精度が低い場合は Gemini に本文から資料種別を自己判定させることを検討。

#### 【最重要設計原則】Reader は「構造解析をしない」

資料の送り元（会社）が変わると Excel の列名・行位置・シート名がすべて変わる。
Reader の仕事は1つだけ: ファイルの中身を「巨大な1つのテキスト」としてフラットにダンプすること。
どんな構造かの解析は Gemini に任せる。Python 側に「こういう形のはず」という固定概念を持たせない。

#### `excel_reader.py` と `word_reader.py`（parser.py への薄いラッパ）

```python
# apps/backend/app/readers/excel_reader.py
from apps.backend.app.agents.data_extractor.parser import parse_file
from .source_document import SourceDocument, infer_document_kind

def read_excel(file_path: str) -> SourceDocument:
    """
    既存 parser.py の _parse_excel を再利用して SourceDocument を構築する。
    Excel 読み込みロジックを二重実装しない。
    """
    text_content = parse_file(file_path)   # 全シート行ダンプを返す
    from openpyxl import load_workbook
    wb = load_workbook(file_path, read_only=True, data_only=True)
    sheet_names = wb.sheetnames
    wb.close()
    return SourceDocument(
        source_file=file_path,
        source_type="excel",
        document_kind=infer_document_kind(file_path),
        text_content=text_content,
        metadata={"sheets": list(sheet_names)},
    )
```

```python
# apps/backend/app/readers/word_reader.py
from apps.backend.app.agents.data_extractor.parser import parse_file
from .source_document import SourceDocument, infer_document_kind

def read_word(file_path: str) -> SourceDocument:
    """既存 parser.py の _parse_word を再利用して SourceDocument を構築する。"""
    text_content = parse_file(file_path)
    return SourceDocument(
        source_file=file_path,
        source_type="word",
        document_kind=infer_document_kind(file_path),
        text_content=text_content,
        metadata={},
    )
```

#### `pdf_reader.py`（本当に新規）

- pypdf でテキスト抽出を試みる
- 抽出テキストが 100 文字以下（スキャン PDF の可能性）の場合、Gemini multimodal にフォールバック
- **【安全弁】`MAX_PAGES_PER_FILE = 50` を定数として定義し、超過ファイルは先頭 50 ページのみ処理して WARNING ログを出す**

```python
# apps/backend/app/readers/pdf_reader.py
import logging
from .source_document import SourceDocument, infer_document_kind

logger = logging.getLogger(__name__)
MAX_PAGES_PER_FILE = 50

def read_pdf(file_path: str) -> SourceDocument:
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    total_pages = len(reader.pages)

    if total_pages > MAX_PAGES_PER_FILE:
        logger.warning(
            f"[pdf_reader] {file_path} は {total_pages} ページあり上限 {MAX_PAGES_PER_FILE} を超えています。"
            f"先頭 {MAX_PAGES_PER_FILE} ページのみ処理します。"
        )

    pages_to_process = reader.pages[:MAX_PAGES_PER_FILE]

    text = "\n".join(
        f"[ページ{i+1}] {page.extract_text() or ''}"
        for i, page in enumerate(pages_to_process)
    )

    # スキャン PDF 判定 → Gemini multimodal フォールバック
    if len(text.strip()) < 100:
        text = _extract_via_gemini_multimodal(file_path, pages_to_process)

    return SourceDocument(
        source_file=file_path,
        source_type="pdf",
        document_kind=infer_document_kind(file_path),
        text_content=text,
        metadata={"total_pages": total_pages, "processed_pages": len(pages_to_process)},
    )
```

#### `source_document.py`（共通 dataclass）

```python
# apps/backend/app/readers/source_document.py
from dataclasses import dataclass
from pathlib import Path

@dataclass
class SourceDocument:
    source_file: str
    source_type: str
    document_kind: str
    text_content: str
    metadata: dict

_KIND_KEYWORDS = {
    "見積書":    ["見積", "estimate"],
    "物量データ": ["物量", "quantity"],
    "工程表":    ["工程", "schedule"],
}

def infer_document_kind(file_path: str) -> str:
    name = Path(file_path).stem.lower()
    for kind, keywords in _KIND_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return kind
    return "不明"

def select_reader(file_path: str):
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
    raise ValueError(f"未対応のファイル形式: {suffix}")
```

---

### STEP 4: Gemini Structured Output 抽出モジュール（既存 mapper.py + validator.py を拡張）

**【注意】新規作成ではなく既存の拡張**。  
`data_extractor/mapper.py`（LLM 抽出）と `validator.py`（型変換・信頼度・source_location）がすでにこの役割を担っている。  
重複モジュールを作ると既存の E2E テスト（33件）を壊すリスクがある。既存コードを読んでから最小限の拡張として実装すること。

**必須要件**:
1. `response_schema`（Pydantic モデルまたは JSON Schema）を使い、JSON が**必ず**指定フォーマットで返るようにする（現状 `call_gemini` はテキスト返却のみ。structured output を追加する際は既存の JSON パース方式と統一すること）
2. 各フィールドに `source_location`（どのファイルのどの箇所か）を含める（既存 `validator.py` の実装と統一すること）
3. **【単位】金額フィールドは「円単位のまま」返すこと。千円変換は STEP 6（書き込み直前）で行う**
4. **【新規追加】計算仕様（FormulaSpec）の抽出も対象に加える**

```python
EXTRACTION_SYSTEM_PROMPT = """
あなたは建設工事資料から情報を抽出するアシスタントです。
以下のルールを厳守してください:
- 指定されたJSONフォーマットのみで回答する
- 金額は【円単位の数値】をそのまま返す（千円変換はしない。変換は書き込み時に行う）
- 資料に記載がないフィールドは null を返す（推測・補完は禁止）
- 確信が持てない場合は confidence: low とし、その理由を source_context に必ず記載する
- どの箇所から抽出したかを source_context に必ず記載する
- 資料の種類ヒント（document_kind）が提供される場合はそれを手がかりに使う
"""

# document_kind を Gemini へのプロンプトに組み込む
DOCUMENT_KIND_HINTS = {
    "見積書":    "この資料は「参考見積書」です。工事件名・御見積金額・工事内訳・実施条件を含む可能性があります。",
    "物量データ": "この資料は「解体物量データ」です。機器ID・機器名称・口径・重量・作業区域などの機器一覧を含む可能性があります。",
    "工程表":    "この資料は「工事工程表」です。工事件名・予定工期（開始日・終了日）・作業項目を含む可能性があります。",
    "不明":      "この資料の種類は不明です。記載されているすべての情報から関連フィールドを探してください。",
}

# 計算仕様の抽出（mapper.py への追加部分）
# 資料内に計算式・係数が記載されている場合は FormulaSpec として抽出する。
#
# 例: 資料に「工数 = 重量(t) × 基準工数(人日/t)、基準工数=2.78」とある場合:
# {
#   "formula_name": "配管工数",
#   "expression": "weight * manhour_per_ton",
#   "variables": {"weight": 1.5, "manhour_per_ton": 2.78},
#   "gemini_result": 4.17,      ← Gemini が計算して申告（Python が後で検証）
#   "result_unit": "人日",
#   "source_location": {"file": "歩掛計算シート.xlsx", "sheet": "配管基準工数", "row": 15}
# }
#
# 計算式が見つからない場合は formula_specs: [] として返す。
FORMULA_SPEC_PROMPT_ADDITION = """
資料内に計算式、係数テーブル、積算根拠が記載されている場合は、
formula_specs フィールドに FormulaSpec のリストとして抽出してください。
- expression は Python の四則演算式（round/ceil/floor/min/max 使用可）として表現してください
- 変数名は英語の snake_case にしてください
- gemini_result にあなたが計算した結果の数値を記載してください
"""
```

**計画/実績の判定ロジック**:
```python
PLANNING_KEYWORDS = ["参考見積書", "申請", "予定", "計画"]
ACTUAL_KEYWORDS = ["実績報告", "完了報告", "確定", "検収"]

def infer_plan_actual(source_doc: SourceDocument) -> str:
    """文書タイトル・本文から「計画」または「実績」を推定する。"""
    text = source_doc.text_content
    # キーワードマッチで先に判定、不明なら Gemini に問い合わせ
    ...
```

---

### STEP 5: N:1 マージ・競合解決モジュール

`apps/backend/app/merger/field_merger.py` に実装。

優先順位設定は将来的に YAML 外部化を推奨（会社・様式ごとに上書きできるようにするため）。PoC では以下のハードコードで可。

```python
# ソースタイプ別の優先順位（小さいほど優先）
# TODO(PoC後): YAML 外部化して会社・様式ごとに設定できるようにする
SOURCE_PRIORITY = {
    "見積書": 1,
    "工程表": 2,
    "物量データ": 3,
    "その他": 99,
}

FIELD_SOURCE_OVERRIDE = {
    "工期開始日": "工程表",
    "工期終了日": "工程表",
    "総額": "見積書",
    "実施内容": "見積書",
}

def merge_extractions(
    extractions: list[dict],  # [{ "source_file": ..., "fields": {...} }, ...]
) -> tuple[dict, list[dict]]:
    """
    複数ソースの抽出結果をマージする。

    Returns:
        merged: マージ済みフィールド dict
        conflicts: 競合が発生したフィールドのリスト（チャット確認用）
    """
    merged = {}
    conflicts = []
    # 解体機器リストは別途 normalize_equipment_list() で名寄せしてから格納
    ...

def normalize_equipment_list(
    raw_lists: list[list[dict]],
) -> list[dict]:
    """
    複数ソースの解体機器リストを名寄せ（重複排除）して統合する。

    【なぜ必要か】
    会社によって同じ機器を異なる名前で記載する。
    例: 物量データ "配管（50A）" ↔ 見積書 "既設50A配管撤去"
    → そのまま結合すると同一機器が2行になる。

    【PoC での簡略化】
    単純結合のみ（重複が起きうるが PoC 段階では許容）。
    重複候補は conflicts に積んで人間確認に回す。
    """
    # TODO(PoC後): Gemini を使った名寄せロジックを実装する
    # 現在は単純結合（重複が起きうるが PoC 段階では許容）
    combined = []
    for lst in raw_lists:
        combined.extend(lst)
    return combined
```

---

### STEP 6: MRC1 ライターの writable 対応（既存 form_generation_pipeline を拡張）

**【注意】新規作成ではなく既存の拡張**。  
`pipelines/form_generation_pipeline.py` の `generate_form_from_dict()` がすでに dict → Excel（通常フィールド＋表形式＋source_location）を担っている。  
`tabular_handler.write_tabular_section` と `cell_writer.write_to_cell` も既存。再定義しないこと。

**追加する機能**:
1. `writable: false` のセルへの書き込みをスキップ
2. **【単位変換】書き込み直前に `unit: 千円` フィールドの値を円 → 千円に変換**
3. `write_tabular_section` に `max_rows=200` 安全弁を追加（既存関数の引数拡張）

#### `unit_converter.py`（新規・純粋関数）

```python
# apps/backend/app/core/unit_converter.py
import re

UNIT_DIVISORS = {
    "千円": 1_000,
    "万円": 10_000,
}

def parse_to_float(value) -> float | None:
    """
    validator.py が文字列のまま返す数値を float に変換する。
    "143,500,000" や "143500000円" などを受け付ける。

    【注意】validator.py は _validate_number で数値を文字列のまま保持する（元の形式を保持）。
    unit_converter はその文字列を float に正規化してから変換すること。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,，円千万億\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None

def convert_unit(value, from_unit: str, to_unit: str) -> float | None:
    """
    書き込み直前の単位変換（円 → 千円など）。
    パイプライン内では円のまま扱い、ここでのみ変換する。

    【前提】value は円単位の数値（Gemini が円で返すよう指示してある）。
    Gemini が "143,500千円" のように千円で返してきた場合は変換後の値がおかしくなるため、
    parse_to_float が None を返したり極端な値になった場合は conflicts に積むこと。

    例:
        convert_unit(143_500_000, from_unit="円", to_unit="千円")
        → 143_500.0
    """
    numeric = parse_to_float(value)
    if numeric is None:
        return None
    if from_unit == to_unit:
        return numeric
    if to_unit in UNIT_DIVISORS:
        return numeric / UNIT_DIVISORS[to_unit]
    raise ValueError(f"未対応の変換: {from_unit} → {to_unit}")
```

#### `generate_form_from_dict()` の拡張箇所（通常フィールド）

```python
# 既存の書き込みループに追加する処理
from apps.backend.app.core.unit_converter import convert_unit

skipped_cells = []

for field_name, field_def in yaml_config["extraction_schema"].items():
    # 追加1: writable: false のセルはスキップ
    if not field_def.get("writable", True):
        skipped_cells.append(field_name)
        continue

    value = merged_data.get(field_name, {}).get("value")
    if value is None:
        continue

    # 追加2: 書き込み直前に単位変換（円 → 千円）
    target_unit = field_def.get("unit")
    if target_unit and target_unit != "円":
        value = convert_unit(value, from_unit="円", to_unit=target_unit)
        if value is None:
            # 変換失敗（Gemini が円以外の単位で返してきた可能性）
            logger.warning(f"[unit_converter] {field_name} の単位変換に失敗しました。元の値: {merged_data[field_name].get('value')}")
            continue

    cell_address = resolve_cell_address(field_name, field_def, yaml_config)
    if cell_address:
        write_to_cell(workbook, sheet_name, cell_address, value)
```

#### 表形式セクション（解体機器表）の単位変換

解体機器表の費用列（`計画_費用` / `実績_費用`）も千円変換が必要。  
`tabular_handler.write_tabular_section` を拡張し、YAML の `columns` 定義に `unit` を追加できるようにする。

```yaml
# frames/frameB/MRC1.yaml の columns への追加例
columns:
  - {name: 計画_費用, column: J, unit: 千円}
  - {name: 実績_費用, column: N, unit: 千円}
  # unit が未定義の列は変換しない（デフォルト）
```

```python
# tabular_handler.py への追加
for col_def in section.get("columns", []):
    col_name = col_def["name"]
    col_unit = col_def.get("unit")
    value = row_data.get(col_name)
    if col_unit and col_unit != "円":
        value = convert_unit(value, from_unit="円", to_unit=col_unit)
    ...
```

---

### STEP 7: FastAPI エンドポイントの更新

複数ファイル受け付けに変更。**Gemini 呼び出しが複数回入るため、3ファイルでも 30〜60 秒かかる可能性がある。**
同期的に処理するとブラウザ・ゲートウェイがタイムアウトするため、BackgroundTasks で非同期化する。

```python
import uuid
from fastapi import BackgroundTasks

# ジョブの状態を管理する簡易ストア（PoC用。本番は Redis 等に置き換え）
job_store: dict[str, dict] = {}

@app.post("/api/transcribe/mrc1")
async def transcribe_mrc1(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    sheet: str = Form(default="MRC1"),
    frame: str = Form(default="frameB"),
):
    """
    N対1 転記エンドポイント（非同期ジョブ方式）。
    受け付けたら即座に job_id を返す。処理はバックグラウンドで実行。
    フロントエンドは GET /api/jobs/{job_id} でポーリングして完了を確認する。
    """
    job_id = str(uuid.uuid4())
    job_store[job_id] = {"status": "running", "progress": 0, "result": None}

    # ファイルの中身をここで読み込む（BackgroundTask に渡す前に）
    file_contents = [(f.filename, await f.read()) for f in files]

    background_tasks.add_task(
        _run_transcription_pipeline,
        job_id=job_id,
        file_contents=file_contents,
        sheet=sheet,
        frame=frame,
    )

    return {"job_id": job_id, "status": "accepted"}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """ジョブの進捗・結果を返す。フロントエンドはこれをポーリングする。"""
    if job_id not in job_store:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_store[job_id]


def _run_transcription_pipeline(
    job_id: str,
    file_contents: list[tuple[str, bytes]],
    sheet: str,
    frame: str,
):
    """
    実際のパイプライン処理（BackgroundTasks からスレッドプールで実行される）。

    【注意】async def にすると call_gemini（同期）がイベントループを占有して
    他のリクエストが 30-60 秒固まる。sync def にして FastAPI にスレッドプール実行させること。
    """
    try:
        source_docs = []
        for filename, content in file_contents:
            reader = select_reader_from_bytes(filename, content)
            source_docs.append(reader)
            job_store[job_id]["progress"] += int(50 / len(file_contents))

        extractions = [extract_fields(doc, yaml_config) for doc in source_docs]
        formula_results = [execute_formula(spec) for ext in extractions for spec in ext.get("formula_specs", [])]
        merged, conflicts = merge_extractions(extractions)

        # formula_results のうち needs_review のものを conflicts に追加
        for fr in formula_results:
            if fr.needs_review:
                conflicts.append({
                    "type": "formula_inconsistency",
                    "formula_name": fr.formula_name,
                    "python_result": fr.python_result,
                    "gemini_result": fr.gemini_result,
                    "note": fr.discrepancy_note,
                    "source_location": fr.source_location,
                })

        cell_mappings, processed_sheets = generate_form_from_dict(
            input_data=merged,
            source_metadata={},
            template_excel_path=template_path,
            result_excel_path=output_path,
            frame_name=frame,
        )

        job_store[job_id] = {
            "status": "completed",
            "progress": 100,
            "result": {
                "output_path": output_path,
                "skipped_cells": [],     # generate_form_from_dict から取得
                "conflicts": conflicts,
                "formula_results": [
                    {"name": fr.formula_name, "consistent": fr.is_consistent,
                     "source_location": fr.source_location}
                    for fr in formula_results
                ],
            }
        }
    except Exception as e:
        job_store[job_id] = {"status": "failed", "progress": 0, "error": str(e)}
```

---

### STEP 8: 手動確認スクリプト

`scripts/check_n_to_1_pipeline.py` を作成:

```bash
# 使い方
PYTHONPATH=. uv run python scripts/check_n_to_1_pipeline.py \
  --files data/見積書.pdf data/物量データ.xlsx data/工程表.xlsx \
  --sheet MRC1 \
  --frame frameB \
  --output output/MRC1_result.xlsx
```

実行すると以下を出力すること:
- 各ファイルから抽出されたフィールド一覧（source_location 付き）
- 競合が発生したフィールドと解決結果
- skipped_cells（writable:false でスキップされたセル一覧）
- 計算仕様の検証結果（Python 再計算値・Gemini 申告値・一致/不一致・source_location）

---

## 制約・禁止事項

1. **数式セル上書き禁止**: `writable: false` のセルは書き込みをスキップ
2. **LLM への算術委譲禁止**: 計算の実行は formula_executor（Python）のみ
3. **特定計算式のハードコード禁止**: 歩掛テーブル等は作らない。式と変数は資料から Gemini が抽出
4. **単位変換のタイミング**: パイプライン全体を通じて円で統一。変換は書き込み直前（unit_converter.py）のみ。validator.py は数値を文字列のまま返す（単位変換なし）ので二重変換にはならないが、書込前に float への変換（`parse_to_float`）が必要
5. **表形式の費用列も単位変換対象**: 通常フィールドの単位変換だけでなく、`計画_費用` / `実績_費用` 列も千円変換すること。YAML columns 定義に `unit` を追加する
6. **セル番地の LLM 判断禁止**: 固定レイアウト部分は `frames/frameB/MRC1.yaml` から取得
7. **既存テストを壊さない**: 実装前後で `pytest apps/backend/tests/` が全件パス
8. **git 操作の注意**: `git restore` による上書きに注意。変更前に必ず `git status` 確認
9. **PDF ページ数上限**: `MAX_PAGES_PER_FILE = 50`。超過分は先頭 50 ページのみ処理し WARNING ログを出す
10. **解体機器表の行数上限**: `max_rows=200`。超過した場合は先頭 200 行のみ書き込み WARNING ログを出す
11. **パイプライン関数は sync def**: `_run_transcription_pipeline` は `def`（同期）にすること。`async def` にすると同期の `call_gemini` がイベントループを 30-60 秒占有して他リクエストが固まる
12. **パス規約**: 新規ファイルはすべて `apps/backend/app/` 配下に置く（`app/readers/`, `app/merger/`, `app/tools/`）。既存コードと import 規約を統一すること

---

## テスト優先度

| ファイル | 優先度 | 理由 |
|---|---|---|
| `tests/tools/test_formula_executor.py` | 最高 | LLM 不要・決定論的。safe_eval のセキュリティテスト（危険コードを弾くこと）必須 |
| `tests/tools/test_unit_converter.py` | 最高 | 単純だが桁ずれバグの防止ライン。tabular 費用列の変換も含めること |
| `tests/merger/test_field_merger.py` | 高 | 競合解決ロジックの仕様確認 |
| `tests/writers/test_mrc1_writer.py` | 高 | writable:false スキップ・単位変換・max_rows の確認 |
| `tests/extractors/test_gemini_extractor.py` | 中 | Gemini モックで JSON スキーマ検証 |

---

## 未解決の TODO（実装完了後に確認が必要）

- [ ] 歩掛計算シート（`歩掛2025-0-000-06-02` 等）を**入力資料として**受領できるか確認（PoC で formula_executor に渡せる形式か）
- [ ] `全体支払い対象金額`（G19, K19）が数式セルかどうか確認 → `writable` フラグに反映
- [ ] 総額の税込/税抜の扱い（単位変換ルールに影響）
- [ ] N:1 マージの優先順位（見積書 > 工程表 > 物量データ）をクライアントと合意

---

## 参考コマンド

```bash
# 依存ライブラリ追加（必要に応じて）
uv add pypdf python-docx

# テスト実行
PYTHONPATH=. uv run pytest apps/backend/tests/ -v

# 単一テスト実行
PYTHONPATH=. uv run pytest apps/backend/tests/tools/test_formula_executor.py -v
```
