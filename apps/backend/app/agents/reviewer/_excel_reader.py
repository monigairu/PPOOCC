"""
F2/F3ナレッジ Excel 読み込み専用モジュール

knowledge_loader.py（検索バックエンド）とは独立して使用する。
ingest_knowledge.py などのデータ投入スクリプトから直接呼び出す。

Phase が変わっても Excel をソースとして読み込む処理はここで管理する。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = Path("data/knowledge")
_SCHEMA_DIR = _KNOWLEDGE_DIR / "schema"

# ver5.3 平坦形式（1メッセージ=1行）の正準列（REQUIREMENTS.md §0-7 R1）。
# BigQuery 平坦テーブル・Agent Search 索引のスキーマ契約として使う。
# 各キーは schema YAML の fixed_columns キーおよび flatten_qa の生成キーと一致させる。
VER53_COLUMNS = (
    "id",                 # ID
    "message_id",         # メッセージID（flatten_qa が生成: {id}_{round:02d}）
    "start_date",         # 起票日
    "dept_group",         # 起票者所属G
    "author",             # 起票者
    "ref_knowledge_id",   # 参照先ナレッジID
    "submission_timing",  # 提出タイミング
    "confirm_year",       # 確認年度
    "plant_site",         # 該当発電所
    "plant_unit",         # 該当プラント
    "cost_category",      # 該当費目
    "construction_name",  # 該当工事
    "reference_url",      # 該当資料
    "message_content",    # メッセージ内容（検索対象テキスト・flatten_qa が生成）
)

# ver5.3 本体列に加えて検索フィルタ・権限制御に使う付帯列（RAG_VERIFICATION.md §3-3）
VER53_AUX_COLUMNS = (
    "utility_name",       # 電力会社（正規化前。正規化は ingest 側で適用）
    "reactor_type",       # 炉型（BWR/PWR）
    "sheet_name",         # 由来シート（KNI_1G_01 等＝提出タイミング分岐の後ろ盾）
    "message_direction",  # nuro / denryoku（flatten_qa が生成）
    "round",              # やりとり回数（flatten_qa が生成）
)


def to_ver53_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    read_all_f3() の平坦レコードを ver5.3 の正準列＋付帯列に射影する。

    - VER53_COLUMNS / VER53_AUX_COLUMNS にあるキーだけを残す（余分なキーは落とす）
    - 欠けているキーは "" で埋める（round のみ 0）
    → BigQuery ロード・Agent Search 索引が常に同一スキーマの行を受け取れる。
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        row: dict[str, Any] = {}
        for key in VER53_COLUMNS + VER53_AUX_COLUMNS:
            default: Any = 0 if key == "round" else ""
            row[key] = record.get(key, default)
        rows.append(row)
    return rows


def _col_letter_to_idx(col: str) -> int:
    result = 0
    for ch in col.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _infer_direction(key: str) -> str:
    k = key.lower()
    if "nuro" in k or "question" in k:
        return "nuro"
    if "denryoku" in k or "reply" in k or "answer" in k:
        return "denryoku"
    return "unknown"


def _discover_schemas(frame_prefix: str) -> list[dict]:
    if not _SCHEMA_DIR.exists():
        return []
    schemas = []
    for path in sorted(_SCHEMA_DIR.glob(f"{frame_prefix}_*_schema.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                schemas.append(yaml.safe_load(f))
        except Exception as e:
            logger.warning("スキーマ読み込みエラー: %s (%s)", path, e)
    return schemas


def _read_excel_by_schema(
    schema: dict,
    file_path: Path,
) -> tuple[list[dict[str, Any]], str]:
    layout = schema.get("layout", {})
    data_start_row: int = layout.get("data_start_row", 7)
    loader_cfg: dict = schema.get("loader_config", {})
    id_col: str = loader_cfg.get("id_column", "A")
    id_col_idx: int = _col_letter_to_idx(id_col)

    excel_sheet = schema.get("excel_sheet")
    df_raw = pd.read_excel(
        file_path,
        sheet_name=excel_sheet if excel_sheet else 0,
        header=None,
        engine="openpyxl",
        dtype=str,
    )
    df_raw = df_raw.fillna("")

    utility_name = ""
    for _meta_key, meta_val in schema.get("meta_cells", {}).items():
        cell_addr: str = meta_val.get("cell", "")
        if not cell_addr:
            continue
        col_letter = "".join(c for c in cell_addr if c.isalpha())
        row_num = int("".join(c for c in cell_addr if c.isdigit()))
        c_idx = _col_letter_to_idx(col_letter)
        r_idx = row_num - 1
        if r_idx < len(df_raw) and c_idx < df_raw.shape[1]:
            utility_name = str(df_raw.iat[r_idx, c_idx]).strip()
        break

    if data_start_row - 1 >= len(df_raw):
        return [], utility_name

    data_df = df_raw.iloc[data_start_row - 1:].copy().reset_index(drop=True)
    data_df = (
        data_df.replace("", pd.NA)
               .ffill(axis=0)
               .fillna("")
               .astype(str)
    )

    fixed_columns: list[dict] = schema.get("fixed_columns", [])
    qa_config: dict | None = schema.get("repeating_qa_columns")
    flatten_qa: bool = schema.get("output_model", {}).get("flatten_qa", True)
    records: list[dict[str, Any]] = []

    for _, row in data_df.iterrows():
        id_val = row.iloc[id_col_idx].strip() if id_col_idx < len(row) else ""
        if not id_val or id_val in ("nan", "None"):
            continue

        base: dict[str, Any] = {}
        for col_def in fixed_columns:
            c_idx = _col_letter_to_idx(col_def["col"])
            val = row.iloc[c_idx].strip() if c_idx < len(row) else ""
            base[col_def["key"]] = "" if val in ("nan", "None") else val

        if utility_name:
            base["utility_name"] = utility_name

        # 由来シート（KNI_1G_01 等）。提出タイミング分岐・BigQuery平坦テーブルで使う
        if schema.get("sheet_name"):
            base["sheet_name"] = schema["sheet_name"]

        if qa_config and flatten_qa:
            start_col_idx = _col_letter_to_idx(qa_config["start_col"])
            col_per_round: int = qa_config["col_per_round"]
            max_rounds: int = qa_config["max_rounds"]
            qa_fields: list[dict] = qa_config["fields"]

            for round_num in range(1, max_rounds + 1):
                for field_def in qa_fields:
                    actual_idx = (
                        start_col_idx
                        + (round_num - 1) * col_per_round
                        + field_def["col_offset"]
                    )
                    if actual_idx >= len(row):
                        continue
                    content = row.iloc[actual_idx].strip()
                    if not content or content in ("nan", "None"):
                        continue
                    msg_record = {**base}
                    msg_record["message_id"] = f"{id_val}_{round_num:02d}"
                    msg_record["round"] = round_num
                    msg_record["message_direction"] = _infer_direction(field_def["key"])
                    msg_record["message_content"] = content
                    records.append(msg_record)
        else:
            records.append(base)

    return records, utility_name


def read_all_f2() -> list[dict[str, Any]]:
    """F2ナレッジをExcelから全件読み込む。"""
    schemas = _discover_schemas("f2")
    all_records: list[dict] = []
    for schema in schemas:
        frame = schema.get("frame", "F2").upper()
        sname = schema.get("sheet_name", "")
        excel_file = schema.get("excel_file") or f"{frame}_{sname}.xlsx"
        file_path = _KNOWLEDGE_DIR / excel_file
        if not file_path.exists():
            continue
        try:
            records, _ = _read_excel_by_schema(schema, file_path)
            all_records.extend(records)
        except Exception as e:
            logger.warning("F2読み込みエラー: %s (%s)", file_path, e)
    return all_records


def read_all_f3() -> list[dict[str, Any]]:
    """F3ナレッジをExcelから全件読み込む。"""
    schemas = _discover_schemas("f3")
    all_records: list[dict] = []
    for schema in schemas:
        frame = schema.get("frame", "F3").upper()
        sname = schema.get("sheet_name", "")
        excel_file = schema.get("excel_file") or f"{frame}_{sname}.xlsx"
        file_path = _KNOWLEDGE_DIR / excel_file
        if not file_path.exists():
            continue
        try:
            records, _ = _read_excel_by_schema(schema, file_path)
            all_records.extend(records)
        except Exception as e:
            logger.warning("F3読み込みエラー: %s (%s)", file_path, e)
    return all_records
