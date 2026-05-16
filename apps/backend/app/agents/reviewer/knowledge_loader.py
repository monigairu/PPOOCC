"""
ナレッジ読み込みモジュール

現在の実装（Phase 1）：構造化フィルタ型RAG
  - スキーマYAML駆動でExcelを直接読み込み
  - 権限フィルタ（caller_role/utility_name）＋構造化フィルタ（fee_type）で絞り込み
  - QAを縦持ち展開（1メッセージ=1チャンク）してGeminiプロンプトに直接注入

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Phase 1の制約】

① 同義語・表記ゆれに対応できない
   「費用低減」と「コスト削減」は別単語として扱われる
   → Phase 2のハイブリッド検索（BM25+ベクトル）で解決予定

② reactor_type（炉型）の絞り込みが機能しない
   F3スキーマに reactor_type に相当する列が定義されていない
   → Phase 2でスキーマを拡張して対応

③ 補足資料の写真・図面情報が使えない
   Excelに貼り付けられた写真は現状では無視される
   → Phase 3でGemini 2.0 Flashのマルチモーダル機能で対応

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 2（PoC後半予定）：
  - Vertex AI Search + ハイブリッド検索（BM25+ベクトル）+ Reranking
  - 補足資料のテキスト情報を Tool5 として追加
  - reactor_type の絞り込みをスキーマ拡張で実現
  - このファイルのI/F（引数・戻り値）は変更しない

Phase 3（本番運用後）：
  - Gemini 2.0 Flash/Pro のマルチモーダルで写真・図面を処理
  - Document AI で解体状況図（PPTX）の構造解析
  - Graph RAGの必要性を評価（データ蓄積後に判断）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
本番移行時の注意：
    現在は caller_role="NuRO" をエンドポイント側で固定して呼び出している。
    本番では FastAPI の Depends で検証済み JWT から caller_role を取得して渡す。
    このファイルの変更は不要。エンドポイントに user=Depends(get_current_user) を
    追加して caller_role=user["role"] に変更するだけ。

スキーマファイル検出ルール：
    data/knowledge/schema/f3_*_schema.yaml → F3ナレッジ（電力別問合せ履歴）
    data/knowledge/schema/f2_*_schema.yaml → F2ナレッジ（NuRO内有の知見）
    対応するExcelファイル: data/knowledge/{excel_file キー or FRAME_sheet_name}.xlsx
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


# ─── ユーティリティ ────────────────────────────────────────────────────────────

def _col_letter_to_idx(col: str) -> int:
    """
    Excel列文字→0始まりのDataFrame列インデックスに変換する。
    例: "A"→0, "Z"→25, "AA"→26, "AH"→33
    """
    result = 0
    for ch in col.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _infer_direction(key: str) -> str:
    """フィールドキー名からメッセージ送信者を推定する"""
    k = key.lower()
    if "nuro" in k or "question" in k:
        return "nuro"
    if "denryoku" in k or "reply" in k or "answer" in k:
        return "denryoku"
    return "unknown"


def _discover_schemas(frame_prefix: str) -> list[dict]:
    """
    スキーマファイルを自動検出して読み込む。
    frame_prefix: "f2" または "f3"

    Phase 2 移行時：このファイルのインターフェースを変えずに
    _read_excel_by_schema() の内部実装を Vertex AI Search に差し替える。
    """
    if not _SCHEMA_DIR.exists():
        logger.warning("スキーマディレクトリが存在しません: %s", _SCHEMA_DIR)
        return []
    schemas = []
    for path in sorted(_SCHEMA_DIR.glob(f"{frame_prefix}_*_schema.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                schemas.append(yaml.safe_load(f))
        except Exception as e:
            logger.warning("スキーマファイルの読み込みに失敗しました: %s (%s)", path, e)
    return schemas


# ─── Excelリーダー ──────────────────────────────────────────────────────────────

def _read_excel_by_schema(
    schema: dict,
    file_path: Path,
) -> tuple[list[dict[str, Any]], str]:
    """
    スキーマ定義に基づいてExcelを読み込み、QAを縦持ち（1メッセージ=1行）に展開する。

    Returns:
        (records, utility_name)
        records:      flatten_qa=True なら1メッセージ=1行に展開済みのリスト
        utility_name: F3の meta_cells.electric_company から読んだ会社名（F2は空文字）
    """
    layout = schema.get("layout", {})
    data_start_row: int = layout.get("data_start_row", 7)
    loader_cfg: dict = schema.get("loader_config", {})
    id_col: str = loader_cfg.get("id_column", "A")
    id_col_idx: int = _col_letter_to_idx(id_col)

    # schema に excel_sheet が指定されていれば特定シートを読む（複数シート形式のファイル対応）
    excel_sheet = schema.get("excel_sheet")

    # header=None で全セルを生の値として読む（列インデックスは整数）
    df_raw = pd.read_excel(
        file_path,
        sheet_name=excel_sheet if excel_sheet else 0,
        header=None,
        engine="openpyxl",
        dtype=str,
    )
    df_raw = df_raw.fillna("")

    # F3のみ: meta_cells から電力会社名を取得（各ファイルが1社分）
    utility_name = ""
    for _meta_key, meta_val in schema.get("meta_cells", {}).items():
        cell_addr: str = meta_val.get("cell", "")
        if not cell_addr:
            continue
        col_letter = "".join(c for c in cell_addr if c.isalpha())
        row_num = int("".join(c for c in cell_addr if c.isdigit()))
        c_idx = _col_letter_to_idx(col_letter)
        r_idx = row_num - 1  # 0-indexed
        if r_idx < len(df_raw) and c_idx < df_raw.shape[1]:
            utility_name = str(df_raw.iat[r_idx, c_idx]).strip()
        break  # electric_company は1つ

    # データ行のみ抽出（ヘッダー・凡例行を除く）
    if data_start_row - 1 >= len(df_raw):
        return [], utility_name

    data_df = df_raw.iloc[data_start_row - 1:].copy().reset_index(drop=True)

    # 縦方向セル結合の forward fill（結合セルはopenpyxlで空文字になる）
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
        # ID列が空ならデータなしと判断してスキップ
        id_val = row.iloc[id_col_idx].strip() if id_col_idx < len(row) else ""
        if not id_val or id_val in ("nan", "None"):
            continue

        # 固定列を抽出
        base: dict[str, Any] = {}
        for col_def in fixed_columns:
            c_idx = _col_letter_to_idx(col_def["col"])
            val = row.iloc[c_idx].strip() if c_idx < len(row) else ""
            base[col_def["key"]] = "" if val in ("nan", "None") else val

        # F3: ファイルレベルの電力会社名を付与
        if utility_name:
            base["utility_name"] = utility_name

        # QA繰り返し列を縦持ち展開
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
            # flatten_qa=False の場合はベースレコードをそのまま追加
            records.append(base)

    return records, utility_name


# ─── 公開インターフェース ───────────────────────────────────────────────────────

def load_f3(
    caller_role: str,
    utility_name: str | None,
    reactor_type: str | None = None,
    fee_type: str | None = None,
    sheet_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    F3ナレッジ（電力とNuROの問合せ履歴）を読み込んでフィルタリングして返す。

    caller_role == "電力"：utility_name で自社ファイルのみ返す
    caller_role == "NuRO"：utility_name=None なら全社返す、指定があればその会社のみ

    F3の電力会社名はファイル内の meta_cells（C3セル）から読む。
    utility_name による絞り込みはファイル単位で行う。

    PoC：エンドポイントで caller_role="NuRO" を固定して呼び出す
    本番：caller_role は Depends で検証済み JWT から渡される（このファイルの変更不要）

    Args:
        caller_role:   "NuRO" or "電力"
        utility_name:  電力会社名（None なら全社）
        reactor_type:  炉型での絞り込み（F3スキーマに列がないためPhase2対応）
        fee_type:      費目での絞り込み（cost_category 列で部分一致）
        sheet_name:    特定シートのみ読む場合に指定（"KNI_1G_01" 等）
        limit:         返す件数の上限（Geminiへのトークン制限対策）

    Returns:
        ナレッジ辞書のリスト（1要素=1メッセージ）
    """
    schemas = _discover_schemas("f3")
    if sheet_name:
        schemas = [s for s in schemas if s.get("sheet_name") == sheet_name]

    all_records: list[dict] = []
    for schema in schemas:
        frame = schema.get("frame", "F3").upper()
        sname = schema.get("sheet_name", "")
        # excel_file キーがあればそれを優先、なければ {FRAME}_{sheet_name}.xlsx
        excel_file = schema.get("excel_file") or f"{frame}_{sname}.xlsx"
        file_path = _KNOWLEDGE_DIR / excel_file

        if not file_path.exists():
            logger.debug("F3ナレッジファイルが存在しません（スキップ）: %s", file_path)
            continue

        try:
            records, file_utility = _read_excel_by_schema(schema, file_path)
        except Exception as e:
            logger.warning("F3ファイルの読み込みエラー: %s (%s)", file_path, e)
            continue

        # 権限フィルタ（ファイルレベル: F3 は1ファイル=1社）
        if caller_role == "電力":
            if not utility_name:
                continue
            if file_utility and file_utility != utility_name:
                continue
        elif caller_role == "NuRO":
            if utility_name and file_utility and file_utility != utility_name:
                continue

        # 費目フィルタ（cost_category 列での部分一致）
        if fee_type:
            records = [r for r in records if fee_type in r.get("cost_category", "")]

        all_records.extend(records)

    return all_records[:limit]


def load_f2(
    caller_role: str,
    fee_type: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """
    F2ナレッジ（NuRO内有の知見）を読み込んで返す。

    caller_role == "電力"：空リストを返す（F2はNuROのみ参照可）
    caller_role == "NuRO"：全件返す（fee_type 指定時は絞り込み）

    F2に cost_category 列はないため、fee_type は title・business_category で部分一致。
    Phase 2 で Vertex AI Search への切り替え時に精度を改善する。

    PoC：エンドポイントで caller_role="NuRO" を固定して呼び出す
    本番：caller_role は Depends で検証済み JWT から渡される（このファイルの変更不要）

    Args:
        caller_role: "NuRO" or "電力"
        fee_type:    費目での絞り込み（タイトル・業務カテゴリで部分一致）
        limit:       返す件数の上限

    Returns:
        ナレッジ辞書のリスト（電力の場合は常に空リスト）
    """
    if caller_role == "電力":
        return []

    schemas = _discover_schemas("f2")
    all_records: list[dict] = []

    for schema in schemas:
        frame = schema.get("frame", "F2").upper()
        sname = schema.get("sheet_name", "")
        excel_file = schema.get("excel_file") or f"{frame}_{sname}.xlsx"
        file_path = _KNOWLEDGE_DIR / excel_file

        if not file_path.exists():
            logger.debug("F2ナレッジファイルが存在しません（スキップ）: %s", file_path)
            continue

        try:
            records, _ = _read_excel_by_schema(schema, file_path)
        except Exception as e:
            logger.warning("F2ファイルの読み込みエラー: %s (%s)", file_path, e)
            continue

        # 費目フィルタ（F2は cost_category がないため title・業務カテゴリで代替）
        # Phase 2 で Vertex AI Search に切り替えた際に意味的な検索に改善する
        if fee_type:
            records = [
                r for r in records
                if (
                    fee_type in r.get("business_category", "")
                    or fee_type in r.get("title", "")
                )
            ]

        all_records.extend(records)

    return all_records[:limit]


def load_supplement(
    caller_role: str,
    utility_name: str | None = None,
    fee_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    補足資料（工事概要Excel）からテキスト情報を読み込む。

    Phase 1：テキスト情報のみ抽出（写真は「添付あり」フラグのみ記録）
    Phase 3：Gemini 2.0 Flash のマルチモーダルで写真・図面も処理予定

    対象ファイル：data/knowledge/supplement/ 以下の全 Excel ファイル
    シート名が工事ID（例: 2024-1-002）、A1セルが工事名という構造を想定。

    Args:
        caller_role:   "NuRO" or "電力"（F2と同じ権限制御。電力は空リストを返す）
        utility_name:  将来の権限制御用（Phase 1では未使用）
        fee_type:      費目での絞り込み（テキスト内部分一致）
        limit:         返す件数の上限
    """
    if caller_role == "電力":
        return []

    supplement_dir = _KNOWLEDGE_DIR / "supplement"
    if not supplement_dir.exists():
        logger.debug("補足資料ディレクトリが存在しません（スキップ）: %s", supplement_dir)
        return []

    records: list[dict] = []
    for file_path in sorted(supplement_dir.glob("*.xlsx")):
        try:
            xl = pd.ExcelFile(file_path, engine="openpyxl")
            for sheet in xl.sheet_names:
                df = xl.parse(sheet, header=None, dtype=str).fillna("")
                construction_name = df.iat[0, 0].strip() if len(df) > 0 else sheet
                # 全テキストセルを結合（短い断片は除外）
                text_parts = [
                    cell for row in df.values for cell in row
                    if isinstance(cell, str) and len(cell.strip()) > 3
                ]
                text_content = " ".join(text_parts)[:500]

                # 費目フィルタ（テキスト内部分一致）
                if fee_type and fee_type not in text_content:
                    continue

                records.append({
                    "source_file": file_path.name,
                    "sheet_name": sheet,
                    "construction_name": construction_name,
                    "text_content": text_content,
                    # Phase 3：Gemini 2.0 Flash のマルチモーダルで処理予定
                    "has_images": True,
                })
        except Exception as e:
            logger.warning("補足資料の読み込みエラー: %s (%s)", file_path, e)

    return records[:limit]


def load_all(
    caller_role: str,
    utility_name: str | None,
    reactor_type: str | None = None,
    fee_type: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    F2・F3全ナレッジをまとめて返す。
    reviewer_agent.py の各 Tool から個別に呼び出す設計だが、
    一括取得が必要な場合はこの関数を使う。

    Phase 2 移行後は内部実装のみ Vertex AI Search に切り替える。
    このインターフェース（引数・戻り値）は変更しない。
    """
    f3_records: list[dict] = []
    for schema in _discover_schemas("f3"):
        f3_records.extend(
            load_f3(
                caller_role=caller_role,
                utility_name=utility_name,
                reactor_type=reactor_type,
                fee_type=fee_type,
                sheet_name=schema.get("sheet_name"),
            )
        )

    f2_records = load_f2(caller_role=caller_role, fee_type=fee_type)

    return {"f3": f3_records, "f2": f2_records}
