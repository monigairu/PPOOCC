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

Phase 3（実装済み）：マルチモーダルRAG
  generate_supplement_captions.py で Gemini がキャプション生成済みの画像を
  Vertex AI Search で検索する。Tool4（load_supplement）の内部実装のみ変更。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 3 完了後の残存制約：
① load_similar_work() はデータ未入手のためスタブ（空リスト）
② reactor_type フィルタは Vertex AI Search の struct_data 拡張後に有効化

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
    RERANK_ENABLED,
    RERANK_MODEL,
    VERTEX_SEARCH_F2_BQ_DATASTORE_ID,
    VERTEX_SEARCH_F2_BQ_ENGINE_ID,
    VERTEX_SEARCH_F2_DATASTORE_ID,
    VERTEX_SEARCH_F3_BQ_DATASTORE_ID,
    VERTEX_SEARCH_F3_BQ_ENGINE_ID,
    VERTEX_SEARCH_F3_DATASTORE_ID,
    VERTEX_SEARCH_F2_ENGINE_ID,
    VERTEX_SEARCH_F3_ENGINE_ID,
    VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID,
    VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID,
)

logger = logging.getLogger(__name__)

# ── Vertex AI Search クライアント（遅延初期化・シングルトン）──────────────────
_search_client: discoveryengine.SearchServiceClient | None = None
_rank_client: discoveryengine.RankServiceClient | None = None


def _get_search_client() -> discoveryengine.SearchServiceClient:
    global _search_client
    if _search_client is None:
        _search_client = discoveryengine.SearchServiceClient()
    return _search_client


def _get_rank_client() -> discoveryengine.RankServiceClient:
    global _rank_client
    if _rank_client is None:
        _rank_client = discoveryengine.RankServiceClient()
    return _rank_client


# ── 内部ユーティリティ ────────────────────────────────────────────────────────

def _serving_config(datastore_id: str) -> str:
    """
    データストアに対応するSearch Engineのserving configパスを返す。
    エンジンIDが設定されていればエンジン経由（推奨）、なければデータストア直接。

    Args:
        datastore_id: 検索対象のデータストアID（load_f2/load_f3 等が settings から渡す）。

    Returns:
        Vertex AI Search の servingConfig リソースパス。

    BQ経路（無印キーとBQキーに同じデータストアIDを設定する運用）では
    無印エンジン（旧・直接投入ストアに紐付く）に誤解決しないよう、BQ判定を先に行う。
    """
    engine_id = (
        VERTEX_SEARCH_F2_BQ_ENGINE_ID      if datastore_id == VERTEX_SEARCH_F2_BQ_DATASTORE_ID
        else VERTEX_SEARCH_F3_BQ_ENGINE_ID if datastore_id == VERTEX_SEARCH_F3_BQ_DATASTORE_ID
        else VERTEX_SEARCH_F2_ENGINE_ID    if datastore_id == VERTEX_SEARCH_F2_DATASTORE_ID
        else VERTEX_SEARCH_F3_ENGINE_ID    if datastore_id == VERTEX_SEARCH_F3_DATASTORE_ID
        else VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID if datastore_id == VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID
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


_UTILITY_SUFFIXES = ("株式会社", "（株）", "(株)", "㈱")


def normalize_utility(name: str | None) -> str:
    """電力会社名を正規化する（表記ゆれ吸収）。

    「関東電力株式会社」「関東電力（株）」「関東電力」を全て「関東電力」に揃える。
    ingest（保存値）と検索（フィルタ値）の両側で同じ正規化を通すことで、
    申請様式とナレッジの会社名サフィックス差で自社フィルタが外れるのを防ぐ。
    """
    if not name:
        return ""
    s = str(name).strip()
    for suf in _UTILITY_SUFFIXES:
        s = s.replace(suf, "")
    return s.strip()


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
        records = [_to_record(r) for r in response.results]
    except GoogleAPICallError as e:
        logger.error("Vertex AI Search エラー（datastore=%s）: %s", datastore_id, e)
        return []

    # ハイブリッド検索の結果を semantic-ranker で関連度順に並べ替え（§3-2・Step5）
    return _rerank(effective_query, records)


def _rerank(query: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """検索結果を Ranking API（semantic-ranker）で関連度順に並べ替える。

    Agent Search のハイブリッド検索は件数増加で関連レコードを上位から取りこぼすため、
    クロスエンコーダの意味スコアで並べ替えて surfacing を底上げする（§3-2 採用方針）。
    並べ替えのみで**件数は削らない**（下流の件数期待・炉型後段フィルタと互換）。
    付与した `_rerank_score`（0〜1）は関連性ガードの F2 判定にも使う（§1-18）。

    Args:
        query: 検索クエリ（費目＋工事件名など。RankRequest.query に渡す）。
        records: `_to_record` が返した検索結果のリスト（`message_content` を持つ）。

    Returns:
        `_rerank_score` を付与し関連度降順に並べ替えたレコードのリスト。
        Reranking 無効・レコード0件・API エラー時は入力を（スコア付与せず）そのまま返す
        ＝検索を止めないフォールバック。
    """
    if not RERANK_ENABLED or not records or not GCP_PROJECT_ID:
        return records

    client = _get_rank_client()
    ranking_config = client.ranking_config_path(
        project=GCP_PROJECT_ID,
        location=GCP_LOCATION,
        ranking_config="default_ranking_config",
    )
    # 検索対象テキスト。message_content（F2/F3）が無い補足資料（Tool4）は caption を使う。
    ranking_records = [
        discoveryengine.RankingRecord(
            id=str(i),
            title=str(r.get("construction_name") or r.get("title") or ""),
            content=str(r.get("message_content") or r.get("text_content") or r.get("caption") or ""),
        )
        for i, r in enumerate(records)
    ]
    try:
        response = client.rank(
            request=discoveryengine.RankRequest(
                ranking_config=ranking_config,
                model=RERANK_MODEL,
                top_n=len(records),          # 並べ替えのみ・件数は削らない
                query=query,                 # 呼び出し元（_search）で空クエリは既に補完済み
                records=ranking_records,
            )
        )
    except GoogleAPICallError as e:
        logger.warning("Ranking API エラー（並べ替えをスキップ）: %s", e)
        return records

    # 応答の id（入力インデックス）で並べ替える。各インデックスは1回だけ採用し、
    # 重複id・範囲外id（負数含む）は無視する＝二重採用・取りこぼしを防ぐ。
    reranked: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for rr in response.records:
        try:
            idx = int(rr.id)
        except ValueError:
            continue
        if idx < 0 or idx >= len(records) or idx in consumed:
            continue
        consumed.add(idx)
        records[idx]["_rerank_score"] = float(rr.score)
        reranked.append(records[idx])
    # 応答に含まれなかったレコードは末尾に元順で温存（件数は削らない）
    if len(consumed) != len(records):
        reranked.extend(records[i] for i in range(len(records)) if i not in consumed)
    return reranked


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

    # ver5.3 平坦テーブル（BigQuery索引・列名 cost_category）と旧直接投入
    # （struct キー fee_type）の互換エイリアス。apply_relevance_guard 等の
    # 下流は fee_type を読むため、どちらのデータストアでも同じ形に揃える。
    if "fee_type" not in record and record.get("cost_category"):
        record["fee_type"] = record["cost_category"]

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
        reactor_type:  炉型での絞り込み（BWR/PWR等。struct_data.reactor_type で有効化済み）
        fee_type:      検索クエリ（費目・キーワード）
        sheet_name:    特定スキーマシートのみ検索（Phase 2 では未使用）
        limit:         返す件数の上限

    Returns:
        ナレッジ辞書のリスト
    """
    filter_conditions: dict[str, str] = {}

    # 権限フィルタ（会社名は正規化して表記ゆれを吸収）
    if caller_role == "電力":
        if not utility_name:
            return []
        filter_conditions["utility_name"] = normalize_utility(utility_name)
    elif caller_role == "NuRO" and utility_name:
        filter_conditions["utility_name"] = normalize_utility(utility_name)

    filter_str = _build_filter(filter_conditions)

    # 炉型フィルタ（BWR/PWR）：
    #   Vertex AI Search はサーバ側 filter に使う struct_data フィールドのインデックス反映に
    #   時間がかかる（新規追加フィールドは即時にはフィルタ不可）。確実性を優先し、
    #   reactor_type は struct_data.reactor_type による Python 側の後段フィルタで適用する。
    #   指定時のみ適用（None なら従来どおり炉型で絞らない）。
    fetch_limit = limit if not reactor_type else min(limit * 3, 100)
    records = _search(
        datastore_id=VERTEX_SEARCH_F3_DATASTORE_ID,
        query=fee_type or "",
        filter_str=filter_str,
        limit=fetch_limit,
    )
    if reactor_type:
        records = [r for r in records if str(r.get("reactor_type", "")) == reactor_type][:limit]
    return records


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
    補足資料（Tool4）のキャプション情報を Vertex AI Search から返す。

    Phase 3：generate_supplement_captions.py で生成したキャプションを
             Vertex AI Search でハイブリッド検索する。
    データストアが未設定の場合は空リストにフォールバック（テスト・PoC初期に対応）。

    Args:
        caller_role:   "NuRO" or "電力"（電力は空リストを返す）
        utility_name:  申請電力会社名（フィルタには使用しない。NuROは全社参照可）
        fee_type:      検索クエリに使用する費目
        limit:         返す件数の上限

    Returns:
        補足資料辞書のリスト（caption, construction_name, context_text, source_file 等を含む）
    """
    if caller_role == "電力":
        return []

    if not VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID:
        logger.debug("VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID 未設定: 補足資料は空リストを返します")
        return []

    # NuROは全電力会社の補足資料を参照可能なため utility_name でフィルタしない
    results = _search(
        datastore_id=VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID,
        query=fee_type or "",
        limit=limit,
    )

    # 戻り値を synthesis_node が期待する形式に整形
    records = []
    for r in results:
        caption = r.get("caption") or r.get("message_content", "")
        if not caption:
            continue
        records.append({
            "source_file":       r.get("source_file", ""),
            "sheet_name":        r.get("sheet_name", ""),
            "construction_name": r.get("construction_name", ""),
            "context_text":      r.get("context_text", ""),
            "original_format":   r.get("original_format", ""),
            "text_content":      caption,
            "_doc_id":           r.get("_doc_id", ""),
        })

    return records
