"""
POST /api/upload

アップロードされたファイル（JSON / Excel / Word）を受け取り、
MRC1への転記を実行してセルごとの根拠を返す。

Excel/Wordの場合は data_extractor で構造化JSONに変換してから転記する。
"""
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from apps.backend.app.api.models import UploadResponse, CellMapping
from apps.backend.app.agents.cell_locator.cell_locator_agent import determine_cell_mapping
from apps.backend.app.agents.data_extractor.data_extractor_agent import extract_data_as_source_json
from apps.backend.app.core.excel_io import copy_excel_file, load_workbook_file, save_workbook_file
from apps.backend.app.core.cell_writer import write_to_cell
from apps.backend.app.core.frame_config_loader import load_frame_config
from apps.backend.app.core.cache_manager import get_template_hash, load_mapping_cache, save_mapping_cache
from apps.backend.app.section_handlers.tabular_handler import write_tabular_section

router = APIRouter()

# 出力先フォルダ
OUTPUT_DIR = Path("data/form_generation/output")
UPLOAD_DIR = Path("data/form_generation/input/uploaded")
TEMPLATE_PATH = "data/form_generation/input/templates/frameB_MRC.xlsx"
CACHE_DIR = Path("data/form_generation/cache")

# 対応するファイル形式
SUPPORTED_EXTENSIONS = {".json", ".xlsx", ".xls", ".docx"}


@router.post("/upload", response_model=UploadResponse)
async def upload_and_generate(
    file: UploadFile = File(...),
    sheet_name: str = Form(default="MRC1"),
    frame_name: str = Form(default="frameB"),
):
    """
    ファイルをアップロードしてExcelへの転記を実行する。

    対応形式:
        - .json  → そのまま転記データとして使用
        - .xlsx  → data_extractorでJSONに変換してから転記
        - .docx  → data_extractorでJSONに変換してから転記
    """
    # ── 1. ファイル形式の確認 ──
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"未対応のファイル形式です: {suffix}。対応形式: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    # ── 2. ファイルの内容を読み込み ──
    content = await file.read()

    # ── 3. ファイル形式に応じてJSONデータに変換 ──
    try:
        if suffix == ".json":
            input_data = json.loads(content)
            print(f"   JSONファイルを直接読み込みました: {filename}")
        else:
            input_data = _extract_from_file(content, filename, suffix, sheet_name, frame_name)
            print(f"   {suffix}ファイルからデータを抽出しました: {filename}")

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSONの読み込みに失敗しました: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ファイルの読み込みに失敗しました: {e}")

    # ── 4. セッションIDを生成して出力先を決める ──
    session_id = str(uuid.uuid4())[:8]
    result_path = str(OUTPUT_DIR / f"result_{sheet_name}_{session_id}.xlsx")
    cache_path = str(CACHE_DIR / f"mapping_cache_{sheet_name}.json")

    # ── 5. Excelテンプレートをコピー ──
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        copy_excel_file(TEMPLATE_PATH, result_path)
        workbook = load_workbook_file(result_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excelテンプレートの準備に失敗しました: {e}")

    # ── 6. AIによるセルマッピング（キャッシュ優先）──
    try:
        template_hash = get_template_hash(TEMPLATE_PATH, f"frames/{frame_name}/{sheet_name}.yaml")
        mappings_raw = load_mapping_cache(cache_path, template_hash)

        if mappings_raw is None:
            mappings_raw, reasoning_map = _determine_mapping_with_reasoning(
                input_data, workbook, sheet_name, frame_name
            )
            save_mapping_cache(cache_path, template_hash, mappings_raw)
        else:
            reasoning_map = {key: "キャッシュから取得（前回のAI判定結果）" for key in mappings_raw}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"セルマッピングに失敗しました: {e}")

    # ── 7. Excelへの書き込み ──
    cell_mappings: list[CellMapping] = []
    try:
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
                    cell_mappings.append(CellMapping(
                        field_name=key,
                        cell_address=cell_address,
                        value=str(value),
                        reasoning=reasoning_map.get(key, "根拠情報なし"),
                    ))

        # 表形式セクションの書き込み
        config = load_frame_config(frame_name, sheet_name)
        for section in config.get("sections", []):
            if section.get("type") == "tabular":
                write_tabular_section(workbook, sheet_name, section, input_data)

        save_workbook_file(workbook, result_path)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excel書き込みに失敗しました: {e}")

    return UploadResponse(
        session_id=session_id,
        sheet_name=sheet_name,
        mappings=cell_mappings,
        message=f"{len(cell_mappings)}件のセルへの転記が完了しました（入力: {filename}）",
    )


@router.get("/download/{session_id}")
async def download_result(session_id: str, sheet_name: str = "MRC1"):
    """転記済みExcelファイルをダウンロードする。"""
    from fastapi.responses import FileResponse

    result_path = OUTPUT_DIR / f"result_{sheet_name}_{session_id}.xlsx"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")

    return FileResponse(
        path=str(result_path),
        filename=f"転記結果_{sheet_name}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _extract_from_file(
    content: bytes,
    filename: str,
    suffix: str,
    sheet_name: str,
    frame_name: str,
) -> dict:
    """
    Excel/Wordファイルからdata_extractorを使ってJSONデータを抽出する。
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = UPLOAD_DIR / filename

    try:
        with open(temp_path, "wb") as f:
            f.write(content)

        print(f"   一時ファイル保存: {temp_path}")

        input_data = extract_data_as_source_json(
            source_file=str(temp_path),
            sheet_name=sheet_name,
            frame_name=frame_name,
            verbose=True,
        )

        return input_data

    finally:
        if temp_path.exists():
            temp_path.unlink()


def _determine_mapping_with_reasoning(
    input_data: dict,
    workbook,
    sheet_name: str,
    frame_name: str,
) -> tuple[dict, dict]:
    """
    AIによるマッピング判定を実行し、
    mappings と reasoning を別々に返す。
    """
    import json
    import re
    from pathlib import Path
    from apps.backend.app.core.excel_scanner import scan_label_cells
    from apps.backend.app.core.frame_config_loader import extract_cell_definitions
    from apps.backend.app.core.skill_loader import load_skill, render_skill
    from apps.backend.app.core.ai_client import call_gemini

    try:
        config = load_frame_config(frame_name, sheet_name)
        yaml_cell_defs = extract_cell_definitions(config)
        field_aliases = config.get("field_aliases", {})
    except FileNotFoundError:
        yaml_cell_defs = {}
        field_aliases = {}

    label_map = scan_label_cells(workbook, sheet_name)

    skill_dir = Path("apps/backend/app/agents/cell_locator")
    skill_text = load_skill(skill_dir)
    prompt = render_skill(
        skill_text,
        json_data=json.dumps(input_data, ensure_ascii=False, indent=2),
        label_map=json.dumps(label_map, ensure_ascii=False, indent=2),
        yaml_cell_defs=json.dumps(yaml_cell_defs, ensure_ascii=False, indent=2),
        field_aliases=json.dumps(field_aliases, ensure_ascii=False, indent=2),
    )

    response_text = call_gemini(prompt)

    match = re.search(r"```(?:json)?\s*(.*?)\s*```", response_text, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    else:
        match2 = re.search(r"\{.*\}", response_text, re.DOTALL)
        cleaned = match2.group(0).strip() if match2 else response_text.strip()

    try:
        result = json.loads(cleaned)
        mappings = result.get("mappings", {})
        reasoning = result.get("reasoning", {})
        normalized = {
            k: v if isinstance(v, list) else [v]
            for k, v in mappings.items()
        }
        return normalized, reasoning
    except Exception:
        mappings = determine_cell_mapping(input_data, workbook, sheet_name, frame_name)
        reasoning = {k: "根拠取得に失敗しました" for k in mappings}
        return mappings, reasoning