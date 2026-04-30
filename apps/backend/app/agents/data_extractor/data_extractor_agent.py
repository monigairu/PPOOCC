"""
データ抽出エージェント

委託会社から提出された資料（Word/Excel）を読み込み、
NuRO様式に必要な情報を構造化 JSON として抽出する。

3層アーキテクチャ:
  Layer 1: parser   → ファイルを構造化テキストに変換（決定論的）
  Layer 2: mapper   → テキストをスキーマに紐付け（LLM使用）
  Layer 3: validator → 型変換・必須チェック・信頼度付与（決定論的）

使い方:
    from apps.backend.app.agents.data_extractor.data_extractor_agent import extract_data

    result = extract_data(
        source_file="path/to/estimate.xlsx",
        sheet_name="MRC1",
        frame_name="frameB",
    )
"""
import json

from apps.backend.app.agents.data_extractor.parser import parse_file
from apps.backend.app.agents.data_extractor.mapper import map_to_schema
from apps.backend.app.agents.data_extractor.validator import validate_and_finalize


def extract_data(
    source_file: str,
    sheet_name: str,
    frame_name: str = "frameB",
    verbose: bool = True,
) -> dict:
    """
    委託会社資料から NuRO 様式に必要なデータを抽出する。

    Args:
        source_file: 委託会社資料のファイルパス（.xlsx / .docx）
        sheet_name: 転記先のシート名（例: "MRC1"）
        frame_name: 様式名（例: "frameB"）
        verbose: 処理経過を出力するかどうか

    Returns:
        {
            "data": sample_source.json と同形式の辞書,
            "_metadata": {
                フィールド名: {
                    "confidence": float,
                    "matched_synonym": str,
                    "source_location": str
                }, ...
            },
            "_validation": {
                "total_fields": int,
                "extracted_fields": int,
                "extraction_rate": str,
                "warnings": list,
                "errors": list,
                ...
            }
        }
    """
    if verbose:
        print("=== データ抽出エージェント ===\n")

    # ── Layer 1: Parser（決定論的）──────────────
    if verbose:
        print(f"1. ファイルを読み込み中: {source_file}")

    parsed_text = parse_file(source_file)

    if verbose:
        line_count = parsed_text.count("\n") + 1
        print(f"   ✅ パース完了（{line_count} 行）\n")

    # ── Layer 2: Mapper（LLM 使用）─────────────
    if verbose:
        print(f"2. AI によるフィールド抽出中...")
        print(f"   スキーマ: frames/{frame_name}/{sheet_name}.yaml")

    mapper_result = map_to_schema(
        parsed_text=parsed_text,
        sheet_name=sheet_name,
        frame_name=frame_name,
    )

    if verbose:
        extracted_count = len(mapper_result.get("extracted_data", {}))
        print(f"   ✅ 抽出完了（{extracted_count} フィールド）\n")

    # ── Layer 3: Validator（決定論的）───────────
    if verbose:
        print("3. バリデーション実行中...")

    final_result = validate_and_finalize(
        mapper_result=mapper_result,
        sheet_name=sheet_name,
        frame_name=frame_name,
    )

    if verbose:
        validation = final_result["_validation"]
        print(f"   抽出率: {validation['extraction_rate']}")
        print(
            f"   高信頼: {validation['high_confidence_fields']} フィールド"
        )

        if validation["low_confidence_fields"]:
            print(
                f"   ⚠️  低信頼: {validation['low_confidence_fields']}"
            )
        if validation["warnings"]:
            for w in validation["warnings"]:
                print(f"   ⚠️  {w}")
        if validation["errors"]:
            for e in validation["errors"]:
                print(f"   ❌ {e}")

        print(f"\n   ✅ バリデーション完了")

    if verbose:
        print("\n=== 処理完了 ===")

    return final_result


def extract_data_as_source_json(
    source_file: str,
    sheet_name: str,
    frame_name: str = "frameB",
    verbose: bool = True,
) -> dict:
    """
    extract_data の結果から data 部分のみ返す。

    既存の form_generation_pipeline にそのまま渡せる形式。
    sample_source.json と同じ構造の辞書を返す。
    """
    result = extract_data(
        source_file=source_file,
        sheet_name=sheet_name,
        frame_name=frame_name,
        verbose=verbose,
    )
    return result["data"]
