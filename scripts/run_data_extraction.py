"""
データ抽出パイプラインの実行スクリプト

委託会社資料から NuRO 様式に必要なデータを抽出し、
JSON ファイルとして保存する。

使い方:
    # 抽出のみ（JSONを出力）
    uv run python scripts/run_data_extraction.py --input data/source/estimate.xlsx

    # 抽出 → 転記まで一気通貫
    uv run python scripts/run_data_extraction.py --input data/source/estimate.xlsx --run-pipeline

    # 出力先を指定
    uv run python scripts/run_data_extraction.py --input data/source/estimate.xlsx --output data/extracted/result.json
"""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from apps.backend.app.agents.data_extractor.data_extractor_agent import (
    extract_data,
)


def main() -> None:
    """エントリーポイント。"""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="委託会社資料からデータを抽出する"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="委託会社資料のファイルパス（.xlsx / .docx）",
    )
    parser.add_argument(
        "--sheet",
        type=str,
        default="MRC1",
        help="転記先シート名（デフォルト: MRC1）",
    )
    parser.add_argument(
        "--frame",
        type=str,
        default="frameB",
        help="様式名（デフォルト: frameB）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="抽出結果の保存先（デフォルト: data/extracted/<入力ファイル名>.json）",
    )
    parser.add_argument(
        "--run-pipeline",
        action="store_true",
        help="抽出後、form_generation_pipeline も実行する",
    )
    args = parser.parse_args()

    # 出力パスの決定
    if args.output:
        output_path = args.output
    else:
        input_name = Path(args.input).stem
        output_dir = Path("data/extracted")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{input_name}.json")

    # 抽出実行
    result = extract_data(
        source_file=args.input,
        sheet_name=args.sheet,
        frame_name=args.frame,
    )

    # 結果を保存
    print(f"\n📄 抽出結果を保存: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # data 部分のみ（sample_source.json互換）も保存
    data_only_path = output_path.replace(".json", "_data_only.json")
    with open(data_only_path, "w", encoding="utf-8") as f:
        json.dump(result["data"], f, ensure_ascii=False, indent=2)
    print(f"📄 データのみ保存: {data_only_path}")

    # --run-pipeline が指定された場合は転記まで実行
    if args.run_pipeline:
        print("\n=== 様式自動作成パイプラインも実行 ===\n")

        from apps.backend.app.pipelines.form_generation_pipeline import (
            run_form_generation,
        )

        template_path = f"data/form_generation/input/templates/frameB_MRC.xlsx"
        result_path = f"data/form_generation/output/result_{args.sheet}_extracted.xlsx"
        cache_path = f"data/form_generation/cache/mapping_cache_{args.sheet}.json"

        run_form_generation(
            source_json_path=data_only_path,
            template_excel_path=template_path,
            result_excel_path=result_path,
            cache_path=cache_path,
            sheet_name=args.sheet,
            frame_name=args.frame,
        )


if __name__ == "__main__":
    main()
