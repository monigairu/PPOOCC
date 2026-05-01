"""
様式自動作成パイプライン

JSON データを入力として、Excel テンプレートに値を転記し、
完成した Excel ファイルを出力する一連の処理フロー。
"""
import json
from pathlib import Path

from apps.backend.app.agents.cell_locator.cell_locator_agent import determine_cell_mapping
from apps.backend.app.core.cache_manager import (
    get_template_hash,
    load_mapping_cache,
    save_mapping_cache,
)
from apps.backend.app.core.cell_writer import write_to_cell
from apps.backend.app.core.excel_io import (
    copy_excel_file,
    load_workbook_file,
    save_workbook_file,
)
from apps.backend.app.core.frame_config_loader import load_frame_config
from apps.backend.app.section_handlers.tabular_handler import write_tabular_section


def run_form_generation(
    source_json_path: str,
    template_excel_path: str,
    result_excel_path: str,
    cache_path: str,
    sheet_name: str,
    frame_name: str = "frameB",
) -> None:
    """
    様式自動作成のメインフロー。
    """
    print("=== 様式自動作成パイプライン ===\n")

    # 1. JSON データの読み込み
    print("1. 入力 JSON データを読み込み中...")
    with open(source_json_path, "r", encoding="utf-8") as f:
        input_data: dict = json.load(f)
    print(f"   読み込んだデータ: {input_data}\n")

    # 2. Excel テンプレートのコピー
    print("2. Excel テンプレートをコピー中...")
    Path(result_excel_path).parent.mkdir(parents=True, exist_ok=True)
    copy_excel_file(template_excel_path, result_excel_path)
    print(f"   コピー完了: {result_excel_path}\n")

    # 3. Workbook の読み込み
    print("3. Excel ファイルを読み込み中...")
    workbook = load_workbook_file(result_excel_path)
    if sheet_name not in workbook.sheetnames:
        raise ValueError(
            f"シート '{sheet_name}' が存在しません。"
            f"利用可能なシート: {workbook.sheetnames}"
        )
    print(f"   シート '{sheet_name}' を確認\n")

    # 4. マッピングの取得（キャッシュ優先）
    print("4. セルマッピングを取得中...")
    yaml_path = f"frames/{frame_name}/{sheet_name}.yaml"
    template_hash = get_template_hash(template_excel_path, yaml_path)
    mappings = load_mapping_cache(cache_path, template_hash)

    if mappings is None:
        print("   キャッシュなし → AI による判定を実行")
        mappings = determine_cell_mapping(
            input_data,
            workbook,
            sheet_name,
            frame_name=frame_name,
        )
        save_mapping_cache(cache_path, template_hash, mappings)
    print()

    # 5. 通常フィールドの書き込み（キーと値が単純な文字列のもの）
    print("5. 通常フィールドを書き込み中...")
    for key, value in input_data.items():
        # リスト型（表形式データ）はスキップ
        if isinstance(value, list):
            continue

        cell_addresses = mappings.get(key, [])

        if isinstance(cell_addresses, str):
            cell_addresses = [cell_addresses]

        if not cell_addresses:
            print(f"   ⚠️  {key} ({value}) → マッピング対象外")
            continue

        for cell_address in cell_addresses:
            if cell_address == "不明":
                print(f"   ⚠️  {key} ({value}) → 不明（スキップ）")
                continue
            success = write_to_cell(workbook, sheet_name, cell_address, value)
            if success:
                print(f"   ✅ {key} ({value}) → {cell_address}")
            else:
                print(f"   ❌ {key} ({value}) → {cell_address} 書き込み失敗")

    # 6. 表形式セクションの書き込み
    print("\n6. 表形式セクションを書き込み中...")
    try:
        config = load_frame_config(frame_name, sheet_name)
        for section in config.get("sections", []):
            if section.get("type") == "tabular":
                print(f"   セクション: {section['name']}")
                write_tabular_section(
                    workbook,
                    sheet_name,
                    section,
                    input_data,
                )
    except FileNotFoundError:
        print("   YAML定義なし → スキップ")

    # 7. 結果の保存
    print(f"\n7. 結果を保存中...")
    save_workbook_file(workbook, result_excel_path)
    print(f"   保存完了: {result_excel_path}")

    print("\n=== 処理完了 ===")
    print(f"入力データ:   {source_json_path}")
    print(f"テンプレート: {template_excel_path}")
    print(f"出力結果:     {result_excel_path}")