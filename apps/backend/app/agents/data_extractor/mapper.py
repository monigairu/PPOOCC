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

from apps.backend.app.core.ai_client import call_gemini
from apps.backend.app.core.skill_loader import load_skill, render_skill


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
