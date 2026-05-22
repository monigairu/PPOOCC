"""
様式自動作成パイプライン

JSON データを入力として、Excel テンプレートに値を転記し、
完成した Excel ファイルを出力する一連の処理フロー。

エントリーポイント:
  run_form_generation()       CLIスクリプト用（JSONファイルパスを受け取る）
  generate_form_from_dict()   API用（辞書データを受け取り、マッピング情報を返す）
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
from apps.backend.app.core.frame_config_loader import load_frame_config, extract_cell_definitions
from apps.backend.app.core.settings import CACHE_DIR
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


def generate_form_from_dict(
    input_data: dict,
    source_metadata: dict,
    template_excel_path: str,
    result_excel_path: str,
    frame_name: str = "frameB",
    source_filename: str = "",
) -> tuple[list[dict], list[str]]:
    """
    辞書データからExcel転記を実行し、セルマッピング情報を返す。

    APIから呼び出すためのエントリーポイント。
    frames/{frame_name}/ 配下の全YAML定義シートを自動的に処理する。

    Args:
        input_data:          { フィールド名: 値 } の転記用辞書
        source_metadata:     data_extractor の _metadata（source_location を含む）
        template_excel_path: テンプレートExcelのパス
        result_excel_path:   出力先Excelのパス
        frame_name:          様式名（例: "frameB"）
        source_filename:     アップロードされたファイル名（reasoning に使用）

    Returns:
        (cell_mappings, processed_sheets)
        - cell_mappings: [{"field_name", "cell_address", "value", "reasoning"}, ...]
        - processed_sheets: 転記処理したシート名のリスト
    """
    # 1. frames/{frame_name}/ 配下の全YAMLを列挙（将来の複数シート対応）
    yaml_dir = Path("frames") / frame_name
    sheet_names = sorted(f.stem for f in yaml_dir.glob("*.yaml"))
    if not sheet_names:
        raise FileNotFoundError(f"YAML定義が見つかりません: {yaml_dir}")

    print(f"=== 様式生成パイプライン（API） ===")
    print(f"   処理対象シート: {sheet_names}")

    # 2. テンプレートをコピーしてworkbookを読み込む
    Path(result_excel_path).parent.mkdir(parents=True, exist_ok=True)
    copy_excel_file(template_excel_path, result_excel_path)
    workbook = load_workbook_file(result_excel_path)

    all_cell_mappings: list[dict] = []
    processed_sheets: list[str] = []

    for sheet_name in sheet_names:
        if sheet_name not in workbook.sheetnames:
            print(f"   ⚠️  シート '{sheet_name}' はテンプレートに存在しません（スキップ）")
            continue

        print(f"\n--- シート: {sheet_name} ---")

        # 3. キャッシュ優先でセルマッピング（reasoning付き）を取得
        yaml_path = f"frames/{frame_name}/{sheet_name}.yaml"
        cache_path = str(CACHE_DIR / f"mapping_cache_{sheet_name}.json")
        template_hash = get_template_hash(template_excel_path, yaml_path)
        mappings_raw = load_mapping_cache(cache_path, template_hash)

        if mappings_raw is None:
            print("   キャッシュなし → AIによるマッピング判定")
            mappings_raw, reasoning_map = _determine_mapping_with_reasoning(
                input_data, workbook, sheet_name, frame_name
            )
            save_mapping_cache(cache_path, template_hash, mappings_raw)
        else:
            print("   キャッシュあり → キャッシュを使用")
            reasoning_map = {key: "前回のAI判定結果を使用" for key in mappings_raw}

        # 4. 通常フィールドの書き込み
        for key, value in input_data.items():
            if isinstance(value, list):
                continue

            cell_addresses = mappings_raw.get(key, [])
            if isinstance(cell_addresses, str):
                cell_addresses = [cell_addresses]

            for cell_address in cell_addresses:
                if cell_address == "不明":
                    continue
                success = write_to_cell(workbook, sheet_name, cell_address, value)
                if success:
                    base_reasoning = reasoning_map.get(key, "根拠情報なし")
                    field_meta = source_metadata.get(key, {})
                    source_loc = (
                        field_meta.get("source_location")
                        if isinstance(field_meta, dict) else None
                    )
                    reasoning = (
                        f"{base_reasoning}\n抽出元: {source_filename} | {source_loc}"
                        if source_loc else base_reasoning
                    )
                    all_cell_mappings.append({
                        "field_name": key,
                        "cell_address": cell_address,
                        "value": str(value),
                        "reasoning": reasoning,
                    })

        # 5. 表形式セクションの書き込み＋マッピング収集
        try:
            config = load_frame_config(frame_name, sheet_name)
            for section in config.get("sections", []):
                if section.get("type") != "tabular":
                    continue

                print(f"   表形式セクション '{section['name']}' を書き込み中")
                write_tabular_section(workbook, sheet_name, section, input_data)

                # 書き込んだ表形式セルをマッピングに追加（ハイライト・チャット連動）
                json_key = section.get("json_key", "")
                rows = input_data.get(json_key, [])
                data_start_row = section.get("data_start_row", 30)
                col_map = {
                    col["name"]: col["column"]
                    for col in section.get("columns", [])
                }
                for row_idx, row_data in enumerate(rows):
                    excel_row = data_start_row + row_idx
                    for field_name, value in row_data.items():
                        if field_name not in col_map or not value:
                            continue
                        cell_address = f"{col_map[field_name]}{excel_row}"
                        all_cell_mappings.append({
                            "field_name": f"{json_key}[{row_idx + 1}行目].{field_name}",
                            "cell_address": cell_address,
                            "value": str(value),
                            "reasoning": (
                                f"表形式データ「{json_key}」{row_idx + 1}行目の"
                                f"「{field_name}」列\n"
                                f"抽出元: {source_filename}"
                            ),
                        })
        except FileNotFoundError:
            pass

        processed_sheets.append(sheet_name)

    # 6. 結果を保存
    save_workbook_file(workbook, result_excel_path)
    print(f"\n=== 処理完了: {result_excel_path} ===")

    return all_cell_mappings, processed_sheets


def _determine_mapping_with_reasoning(
    input_data: dict,
    workbook,
    sheet_name: str,
    frame_name: str,
) -> tuple[dict, dict]:
    """
    セルマッピングを取得し、reasoning も合わせて返す。

    固定フィールドは YAML から決定論的に解決するため LLM 不使用。
    YAML未定義フィールドのみ cell_locator_agent 経由で LLM にフォールバック。
    """
    mappings = determine_cell_mapping(input_data, workbook, sheet_name, frame_name)

    try:
        config = load_frame_config(frame_name, sheet_name)
        yaml_cell_defs = extract_cell_definitions(config)
    except FileNotFoundError:
        yaml_cell_defs = {}

    reasoning: dict[str, str] = {}
    for key, cells in mappings.items():
        if key in yaml_cell_defs:
            reasoning[key] = f"YAML定義（frames/{frame_name}/{sheet_name}.yaml）による確定マッピング"
        else:
            reasoning[key] = "AI判定によるマッピング"

    return mappings, reasoning