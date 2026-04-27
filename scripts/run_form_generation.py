"""
様式自動作成パイプラインの実行スクリプト

使い方:
    python scripts/run_form_generation.py --sheet MRC1 --frame frameB
    python scripts/run_form_generation.py --sheet MRC2 --frame frameB
"""
import argparse

from dotenv import load_dotenv

from src.pipelines.form_generation_pipeline import run_form_generation


def main() -> None:
    """エントリーポイント。"""
    load_dotenv()

    # コマンドライン引数の定義
    parser = argparse.ArgumentParser(description="様式自動作成パイプライン")
    parser.add_argument(
        "--sheet",
        type=str,
        default="MRC1",
        help="処理対象のシート名（例: MRC1, MRC2, MOI）",
    )
    parser.add_argument(
        "--frame",
        type=str,
        default="frameB",
        help="様式名（例: frameB）",
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/form_generation/input/sample_source.json",
        help="入力 JSON ファイルのパス",
    )
    args = parser.parse_args()

    # パス設定（シート名・様式名は引数から取得）
    source_json_path = args.input
    template_excel_path = (
        f"data/form_generation/input/templates/frameB_MRC.xlsx"
    )
    result_excel_path = (
        f"data/form_generation/output/result_{args.sheet}.xlsx"
    )
    cache_path = (
        f"data/form_generation/cache/mapping_cache_{args.sheet}.json"
    )

    run_form_generation(
        source_json_path=source_json_path,
        template_excel_path=template_excel_path,
        result_excel_path=result_excel_path,
        cache_path=cache_path,
        sheet_name=args.sheet,
        frame_name=args.frame,
    )


if __name__ == "__main__":
    main()