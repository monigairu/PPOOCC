"""
ナレッジ読み込みモジュール

【設計方針】
  各 Phase で内部実装のみ差し替える。I/F（引数・戻り値）は全 Phase 共通。
  → reviewer_agent.py・APIエンドポイント・フロントエンドへの影響なし。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 1（完了）：構造化フィルタ型RAG
  Excel を直接読み込み、権限・費目で絞り込んで Gemini に渡す。

Phase 2（現在）：Vertex AI Search ハイブリッド検索
  BM25+ベクトル検索で同義語・表記ゆれに対応。
  Reranking で上位 N 件に絞る。
  knowledge_loader.py の内部実装のみ変更。

Phase 3（PoC後半予定）：マルチモーダルRAG
  Gemini 3 で写真・図面（補足資料Excel・PPTX）を処理。
  Tool4（補足資料）の内部実装を拡張する。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 2 の既知の制約：
① load_similar_work()はデータ未入手のためスタブ（空リスト）
② reactor_type フィルタは Vertex AI Search の struct_data 拡張で Phase 2 後半対応
③ 補足資料の写真・図面は Phase 3 対応

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
本番移行時の注意：
  caller_role は現在エンドポイントで "NuRO" 固定。
  本番では FastAPI の Depends で検証済み JWT から取得して渡す。
  このファイルの変更は不要。
"""
from __future__ import annotations

import logging
from typing import Any

from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.exceptions import GoogleAPICallError

from apps.backend.app.core.settings import (
    GCP_LOCATION,
    GCP_PROJECT_ID,
    VERTEX_SEARCH_F2_DATASTORE_ID,
    VERTEX_SEARCH_F3_DATASTORE_ID,
    VERTEX_SEARCH_F2_ENGINE_ID,
    VERTEX_SEARCH_F3_ENGINE_ID,
)

logger = logging.getLogger(__name__)

# ── Vertex AI Search クライアント（遅延初期化・シングルトン）──────────────────
_search_client: discoveryengine.SearchServiceClient | None = None


def _get_search_client() -> discoveryengine.SearchServiceClient:
    global _search_client
    if _search_client is None:
        _search_client = discoveryengine.SearchServiceClient()
    return _search_client


# ── 内部ユーティリティ ────────────────────────────────────────────────────────

def _serving_config(datastore_id: str) -> str:
    """
    データストアに対応するSearch Engineのserving configパスを返す。
    エンジンIDが設定されていればエンジン経由（推奨）、なければデータストア直接。
    """
    engine_id = (
        VERTEX_SEARCH_F2_ENGINE_ID if datastore_id == VERTEX_SEARCH_F2_DATASTORE_ID
        else VERTEX_SEARCH_F3_ENGINE_ID if datastore_id == VERTEX_SEARCH_F3_DATASTORE_ID
        else ""
    )
    if engine_id:
        return (
            f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
            f"/collections/default_collection/engines/{engine_id}"
            f"/servingConfigs/default_config"
        )
    return (
        f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
        f"/collections/default_collection/dataStores/{datastore_id}"
        f"/servingConfigs/default_config"
    )


def _build_filter(conditions: dict[str, str]) -> str:
    """
    フィルタ条件辞書を Vertex AI Search のフィルタ文字列に変換する。

    例: {"utility_name": "AA電力", "fee_type": "解体撤去費"}
        → 'utility_name: ANY("AA電力") AND fee_type: ANY("解体撤去費")'
    """
    parts = [
        f'{key}: ANY("{val}")'
        for key, val in conditions.items()
        if val
    ]
    return " AND ".join(parts)


def _search(
    datastore_id: str,
    query: str,
    filter_str: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Vertex AI Search でハイブリッド検索（BM25+ベクトル）を実行する。

    検索結果を knowledge_loader の共通戻り値形式（list[dict]）に変換して返す。
    query が空の場合は全件スキャン相当のクエリを送る（""は不可のため空白文字を使用）。

    Returns:
        ナレッジ辞書のリスト。content と struct_data を展開したフラット形式。
    """
    if not GCP_PROJECT_ID or not datastore_id:
        logger.warning("GCP設定が不完全です（PROJECT_ID=%s, DATASTORE=%s）", GCP_PROJECT_ID, datastore_id)
        return []

    client = _get_search_client()
    # Vertex AI Search は空クエリを受け付けないため、空の場合は全文スキャン相当にする
    effective_query = query.strip() or "工事"

    request = discoveryengine.SearchRequest(
        serving_config=_serving_config(datastore_id),
        query=effective_query,
        filter=filter_str,
        page_size=min(limit, 100),
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            search_result_mode=(
                discoveryengine.SearchRequest.ContentSearchSpec.SearchResultMode.DOCUMENTS
            ),
        ),
    )

    try:
        response = client.search(request=request)
        return [_to_record(r) for r in response.results]
    except GoogleAPICallError as e:
        logger.error("Vertex AI Search エラー（datastore=%s）: %s", datastore_id, e)
        return []


def _to_record(result: discoveryengine.SearchResponse.SearchResult) -> dict[str, Any]:
    """SearchResult を knowledge_loader 共通の辞書形式に変換する。"""
    doc = result.document

    # struct_data は dict ライクな MapComposite。直接イテレートする
    record: dict[str, Any] = {}
    try:
        for key, value in doc.struct_data.items():
            # protobuf Value の場合と Python ネイティブ型の場合を両方処理
            if hasattr(value, "string_value"):
                record[key] = value.string_value or ""
            elif hasattr(value, "number_value"):
                record[key] = value.number_value
            else:
                record[key] = str(value) if value else ""
    except Exception:
        pass

    # content（検索対象テキスト）を message_content として追加
    if doc.content and doc.content.raw_bytes:
        record["message_content"] = doc.content.raw_bytes.decode("utf-8", errors="replace")

    record["_doc_id"] = doc.id
    return record


# ── 公開インターフェース ───────────────────────────────────────────────────────
# I/F（引数・戻り値）は Phase 1 から変更なし。
# 内部実装（検索バックエンド）のみ Vertex AI Search に差し替え済み。

def load_f2(
    caller_role: str,
    fee_type: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """
    F2ナレッジ（NuRO内有の知見）をVertex AI Searchで検索して返す。

    caller_role == "電力"：空リストを返す（F2はNuROのみ参照可）
    caller_role == "NuRO"：fee_type でハイブリッド検索

    Args:
        caller_role: "NuRO" or "電力"
        fee_type:    検索クエリ（費目・キーワード）
        limit:       返す件数の上限

    Returns:
        ナレッジ辞書のリスト（電力の場合は常に空リスト）
    """
    if caller_role == "電力":
        return []

    filter_str = _build_filter({"caller_role_required": "NuRO"})
    return _search(
        datastore_id=VERTEX_SEARCH_F2_DATASTORE_ID,
        query=fee_type or "",
        filter_str=filter_str,
        limit=limit,
    )


def load_f3(
    caller_role: str,
    utility_name: str | None,
    reactor_type: str | None = None,
    fee_type: str | None = None,
    sheet_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    F3ナレッジ（電力とNuROの問合せ履歴）をVertex AI Searchで検索して返す。

    caller_role == "電力"：utility_name で自社ナレッジのみ返す
    caller_role == "NuRO"：utility_name=None なら全社、指定があればその会社のみ

    Args:
        caller_role:   "NuRO" or "電力"
        utility_name:  電力会社名での絞り込み（None なら全社）
        reactor_type:  炉型での絞り込み（Phase 2 後半で struct_data 拡張予定）
        fee_type:      検索クエリ（費目・キーワード）
        sheet_name:    特定スキーマシートのみ検索（Phase 2 では未使用）
        limit:         返す件数の上限

    Returns:
        ナレッジ辞書のリスト
    """
    filter_conditions: dict[str, str] = {}

    # 権限フィルタ
    if caller_role == "電力":
        if not utility_name:
            return []
        filter_conditions["utility_name"] = utility_name
    elif caller_role == "NuRO" and utility_name:
        filter_conditions["utility_name"] = utility_name

    # reactor_type フィルタ（F3 の struct_data に追加後に有効化）
    # TODO Phase 2 後半: ingest_knowledge.py で reactor_type を struct_data に追加
    # if reactor_type:
    #     filter_conditions["reactor_type"] = reactor_type

    filter_str = _build_filter(filter_conditions)
    return _search(
        datastore_id=VERTEX_SEARCH_F3_DATASTORE_ID,
        query=fee_type or "",
        filter_str=filter_str,
        limit=limit,
    )


def load_similar_work(
    caller_role: str,
    reactor_type: str | None = None,
    fee_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    類似工事データ（Tool3）を返す。

    Phase 2 現在：データ未入手のためスタブ（空リスト）。
    データ入手後、Vertex AI Search の別データストアに投入して有効化する。
    knowledge_loader.py の I/F は変更しない。

    Args:
        caller_role:  "NuRO" or "電力"
        reactor_type: 炉型での絞り込み
        fee_type:     費目での絞り込み
        limit:        返す件数の上限

    Returns:
        類似工事辞書のリスト（現在は常に空リスト）
    """
    # Phase 2 スタブ：データ入手後に VERTEX_SEARCH_SIMILAR_WORK_DATASTORE_ID を追加して有効化
    logger.debug("load_similar_work: データ未入手のためスタブを返します")
    return []


def load_supplement(
    caller_role: str,
    utility_name: str | None = None,
    fee_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    補足資料（Tool4）のテキスト情報を返す。

    Phase 2 現在：data/knowledge/supplement/ の Excel からテキストを読み込む。
    Phase 3：Gemini 3 のマルチモーダルで写真・図面も処理予定。

    Args:
        caller_role:   "NuRO" or "電力"（電力は空リストを返す）
        utility_name:  将来の権限制御用（現在未使用）
        fee_type:      テキスト内での絞り込み
        limit:         返す件数の上限

    Returns:
        補足資料辞書のリスト
    """
    if caller_role == "電力":
        return []

    from pathlib import Path
    import pandas as pd

    supplement_dir = Path("data/knowledge/supplement")
    if not supplement_dir.exists():
        return []

    records: list[dict] = []
    for file_path in sorted(supplement_dir.glob("*.xlsx")):
        try:
            xl = pd.ExcelFile(file_path, engine="openpyxl")
            for sheet in xl.sheet_names:
                df = xl.parse(sheet, header=None, dtype=str).fillna("")
                construction_name = df.iat[0, 0].strip() if len(df) > 0 else sheet
                text_parts = [
                    cell for row in df.values for cell in row
                    if isinstance(cell, str) and len(cell.strip()) > 3
                ]
                text_content = " ".join(text_parts)[:500]

                if fee_type and fee_type not in text_content:
                    continue

                records.append({
                    "source_file": file_path.name,
                    "sheet_name": sheet,
                    "construction_name": construction_name,
                    "text_content": text_content,
                    # Phase 3：Gemini 3 マルチモーダルで処理予定
                    "has_images": True,
                })
        except Exception as e:
            logger.warning("補足資料の読み込みエラー: %s (%s)", file_path, e)

    return records[:limit]
