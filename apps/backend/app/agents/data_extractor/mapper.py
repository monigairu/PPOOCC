"""
Layer 2: マッパー

パーサーが出力した構造化テキストと extraction_schema を
Gemini に渡し、スキーマに定義されたフィールドに紐付けた
抽出結果を JSON として返す。

LLM を使用する唯一のレイヤー。
"""
import json
import re
from pathlib import Path

import yaml

from apps.backend.app.core.ai_client import call_gemini, call_gemini_structured
from apps.backend.app.core.skill_loader import load_skill, render_skill


# ── N対1 転記パイプライン用の定数・スキーマ ──────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """
あなたは建設工事資料から情報を抽出するアシスタントです。
以下のルールを厳守してください:
- 指定されたJSONフォーマットのみで回答する
- 金額は【円単位の数値】をそのまま返す（千円変換はしない。変換は書き込み時に行う）
- 資料に記載がないフィールドは null を返す（推測・補完は禁止）
- 確信が持てない場合は confidence を "low" とし、その理由を source_context に必ず記載する
- どの箇所から抽出したかを source_context に必ず記載する
- 資料の種類ヒント（document_kind）が提供される場合はそれを手がかりに使う
"""

DOCUMENT_KIND_HINTS = {
    "見積書":    "この資料は「参考見積書」です。工事件名・御見積金額・工事内訳・実施条件を含む可能性があります。",
    "物量データ": "この資料は「解体物量データ」です。機器ID・機器名称・口径・重量・作業区域などの機器一覧を含む可能性があります。",
    "工程表":    "この資料は「工事工程表」です。工事件名・予定工期（開始日・終了日）・作業項目を含む可能性があります。",
    "不明":      "この資料の種類は不明です。記載されているすべての情報から関連フィールドを探してください。",
}

FORMULA_SPEC_PROMPT_ADDITION = """
資料内に計算式、係数テーブル、積算根拠が記載されている場合は、
formula_specs フィールドに FormulaSpec のリストとして抽出してください。
- expression は Python の四則演算式（round/ceil/floor/min/max 使用可）として表現してください
- 変数名は英語の snake_case にしてください
- gemini_result にあなたが計算した結果の数値を記載してください
- 計算式が見つからない場合は formula_specs を空リスト [] として返してください
"""

PLANNING_KEYWORDS = ["参考見積書", "申請", "予定", "計画"]
ACTUAL_KEYWORDS = ["実績報告", "完了報告", "確定", "検収"]

# Gemini の confidence 文字列 → float への変換
_CONFIDENCE_MAP = {"high": 0.9, "medium": 0.7, "low": 0.3}

# call_gemini_structured に渡す response_schema
EXTRACTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "extracted_fields": {
            "type": "object",
            "description": "抽出したフィールド値。キーはフィールド名",
        },
        "formula_specs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "formula_name": {"type": "string"},
                    "expression": {"type": "string"},
                    "variables": {"type": "object"},
                    "gemini_result": {"type": "number"},
                    "result_unit": {"type": "string"},
                    "source_location": {"type": "object"},
                },
                "required": ["formula_name", "expression", "variables", "gemini_result", "result_unit"],
            },
        },
    },
    "required": ["extracted_fields", "formula_specs"],
}


def map_to_schema(
    parsed_text: str,
    sheet_name: str,
    frame_name: str = "frameB",
) -> dict:
    """
    構造化テキストから extraction_schema に沿ったデータを抽出する。

    Args:
        parsed_text: parser が出力した構造化テキスト
        sheet_name: 対象シート名（例: "MRC1"）
        frame_name: 様式名（例: "frameB"）

    Returns:
        {
            "extracted_data": { フィールド名: 値, ... },
            "field_metadata": { フィールド名: { confidence, matched_synonym, ... }, ... }
        }
    """
    # 1. extraction_schema を YAML から読み込み
    schema = _load_extraction_schema(frame_name, sheet_name)
    schema_text = yaml.dump(
        schema, allow_unicode=True, default_flow_style=False
    )

    # 2. SKILL.md を読み込んでプロンプトを構築
    skill_dir = Path(__file__).parent
    skill_text = load_skill(skill_dir)
    prompt = render_skill(
        skill_text,
        extraction_schema=schema_text,
        source_content=parsed_text,
    )

    # 3. Gemini に問い合わせ
    print("   🤖 AI による抽出を実行中...")
    response_text = call_gemini(prompt)

    # 4. レスポンスを JSON としてパース
    cleaned_text = _extract_json(response_text)

    try:
        result = json.loads(cleaned_text)

        # extracted_data と field_metadata が含まれていることを確認
        if "extracted_data" not in result:
            print("   ⚠️  AI応答に extracted_data がありません。全体をデータとして扱います")
            result = {
                "extracted_data": result,
                "field_metadata": {},
            }

        if "field_metadata" not in result:
            result["field_metadata"] = {}

        return result

    except json.JSONDecodeError as e:
        print(f"   ❌ AI応答のパースに失敗: {e}")
        print(f"   AI応答内容（先頭500文字）: {response_text[:500]}")
        return {
            "extracted_data": {},
            "field_metadata": {},
            "_error": f"JSON パース失敗: {e}",
        }


def _load_extraction_schema(frame_name: str, sheet_name: str) -> dict:
    """
    YAML ファイルから extraction_schema セクションを読み込む。
    """
    yaml_path = Path("frames") / frame_name / f"{sheet_name}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"様式定義ファイルが見つかりません: {yaml_path}"
        )

    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    schema = config.get("extraction_schema")
    if schema is None:
        raise ValueError(
            f"extraction_schema が定義されていません: {yaml_path}"
        )

    return schema


def _extract_json(text: str) -> str:
    """
    AI の応答から JSON 部分のみを抽出する。

    cell_locator_agent と同じロジックを使用。
    """
    # コードブロック内の JSON を探す
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # コードブロックがない場合、最外の { } を探す
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()

    return text.strip()


def map_to_schema_from_doc(
    source_doc,  # SourceDocument（循環インポート回避のため型ヒントは文字列で受ける）
    sheet_name: str,
    frame_name: str = "frameB",
) -> dict:
    """
    SourceDocument から extraction_schema のフィールドと FormulaSpec を抽出する。

    既存の map_to_schema とシグネチャが異なる新規関数。
    既存の E2E テストには影響しない。

    Returns:
        {
            "extracted_data": { フィールド名: 値（円単位）, ... },
            "field_metadata": { フィールド名: { confidence, source_location, matched_synonym }, ... },
            "formula_specs":  [ FormulaSpec, ... ],
        }
    """
    from apps.backend.app.tools.formula_executor import FormulaSpec

    schema = _load_extraction_schema(frame_name, sheet_name)
    schema_text = yaml.dump(schema, allow_unicode=True, default_flow_style=False)

    kind_hint = DOCUMENT_KIND_HINTS.get(source_doc.document_kind, DOCUMENT_KIND_HINTS["不明"])
    prompt = (
        f"{kind_hint}\n\n"
        f"## 抽出スキーマ（YAML定義）\n{schema_text}\n\n"
        f"## 入力資料の内容\n{source_doc.text_content}\n\n"
        f"{FORMULA_SPEC_PROMPT_ADDITION}"
    )

    print("   🤖 AI による構造化抽出を実行中...")
    raw = call_gemini_structured(
        prompt=prompt,
        response_schema=EXTRACTION_RESPONSE_SCHEMA,
        system_instruction=EXTRACTION_SYSTEM_PROMPT,
    )

    # extracted_fields → extracted_data + field_metadata に変換
    extracted_data: dict = {}
    field_metadata: dict = {}

    for field_name, field_data in raw.get("extracted_fields", {}).items():
        if isinstance(field_data, dict):
            value = field_data.get("value")
            confidence_str = field_data.get("confidence", "medium")
            source_ctx = field_data.get("source_context")
        else:
            # Gemini がフラット値を返してきた場合のフォールバック
            value = field_data
            confidence_str = "medium"
            source_ctx = None

        extracted_data[field_name] = value
        field_metadata[field_name] = {
            "confidence": _CONFIDENCE_MAP.get(confidence_str, 0.5),
            "source_location": source_ctx,
            "matched_synonym": None,
        }

    # スキーマ全フィールドが揃っていることを保証
    for field_name in schema:
        if field_name not in extracted_data:
            extracted_data[field_name] = None
            field_metadata[field_name] = {
                "confidence": 0.0,
                "source_location": None,
                "matched_synonym": None,
            }

    # formula_specs → FormulaSpec dataclass に変換
    formula_specs: list = []
    for spec_data in raw.get("formula_specs", []):
        try:
            spec = FormulaSpec(
                formula_name=spec_data["formula_name"],
                expression=spec_data["expression"],
                variables={k: float(v) for k, v in spec_data.get("variables", {}).items()},
                gemini_result=float(spec_data.get("gemini_result", 0.0)),
                result_unit=spec_data.get("result_unit", ""),
                source_location=spec_data.get("source_location", {}),
            )
            formula_specs.append(spec)
        except (KeyError, ValueError, TypeError):
            pass

    return {
        "extracted_data": extracted_data,
        "field_metadata": field_metadata,
        "formula_specs": formula_specs,
    }


def infer_plan_actual(source_doc) -> str:
    """
    SourceDocument の本文キーワードから「計画」か「実績」かを推定する。

    キーワードマッチで判定できない場合は Gemini に問い合わせる。
    Returns: "計画" | "実績" | "不明"
    """
    text = source_doc.text_content
    plan_count = sum(1 for kw in PLANNING_KEYWORDS if kw in text)
    actual_count = sum(1 for kw in ACTUAL_KEYWORDS if kw in text)

    if plan_count > actual_count:
        return "計画"
    if actual_count > plan_count:
        return "実績"

    # キーワードが同数 → Gemini に判断させる
    prompt = (
        "以下の資料は「計画」フェーズのものですか、それとも「実績」フェーズのものですか？\n"
        "「計画」または「実績」の一語のみで回答してください。"
        "どちらとも判断できない場合は「不明」と回答してください。\n\n"
        f"{text[:2000]}"
    )
    try:
        response = call_gemini(prompt)
        if "実績" in response:
            return "実績"
        if "計画" in response:
            return "計画"
    except Exception:
        pass

    return "不明"
