"""
Vertex AI Search データストア・検索エンジン作成スクリプト（一度だけ実行）

実行方法:
    uv run python scripts/preliminary_review/create_datastores.py

作成されるデータストア:
    nuro-f2-knowledge         ... F2ナレッジ（NuRO内有の知見・直接投入／旧経路）
    nuro-f3-knowledge         ... F3ナレッジ（電力別の問合せ履歴・直接投入／旧経路）
    nuro-supplement-knowledge ... 補足資料キャプション（Phase 3）
    nuro-f2-bq-knowledge      ... F2 ver5.3平坦（BigQuery索引・現行経路）
    nuro-f3-bq-knowledge      ... F3 ver5.3平坦（BigQuery索引・現行経路）

作成される検索エンジン（検索アプリ。データストアだけでは推奨経路の検索ができないため必須）:
    nuro-f2-bq-search ... nuro-f2-bq-knowledge に紐付け
    nuro-f3-bq-search ... nuro-f3-bq-knowledge に紐付け

完了後、出力されたIDを .env に追記してください（現行のBQ経路の最小構成）:
    VERTEX_SEARCH_F2_DATASTORE_ID=nuro-f2-bq-knowledge
    VERTEX_SEARCH_F3_DATASTORE_ID=nuro-f3-bq-knowledge
    VERTEX_SEARCH_F2_BQ_DATASTORE_ID=nuro-f2-bq-knowledge
    VERTEX_SEARCH_F3_BQ_DATASTORE_ID=nuro-f3-bq-knowledge
    VERTEX_SEARCH_F2_BQ_ENGINE_ID=nuro-f2-bq-search
    VERTEX_SEARCH_F3_BQ_ENGINE_ID=nuro-f3-bq-search

その後のデータ投入:
    uv run python scripts/preliminary_review/ingest_knowledge.py --backend bigquery
"""
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google.api_core.exceptions import AlreadyExists
from google.cloud import discoveryengine_v1 as discoveryengine

from apps.backend.app.core.settings import GCP_PROJECT_ID, GCP_LOCATION

_DATASTORES = [
    {
        "datastore_id": "nuro-f2-knowledge",
        "display_name": "NuRO F2 Knowledge (NuRO内有の知見)",
        "env_key": "VERTEX_SEARCH_F2_DATASTORE_ID",
    },
    {
        "datastore_id": "nuro-f3-knowledge",
        "display_name": "NuRO F3 Knowledge (電力別問合せ履歴)",
        "env_key": "VERTEX_SEARCH_F3_DATASTORE_ID",
    },
    {
        "datastore_id": "nuro-supplement-knowledge",
        "display_name": "NuRO Supplement Knowledge (補足資料キャプション)",
        "env_key": "VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID",
    },
    {
        # F3 ver5.3 平坦テーブル（BigQuery）を索引する構造化データストア（§0-7 R3）
        # ドキュメント投入は ingest_knowledge.py --backend bigquery が行う
        "datastore_id": "nuro-f3-bq-knowledge",
        "display_name": "NuRO F3 Knowledge BQ (ver5.3平坦・BigQuery索引)",
        "env_key": "VERTEX_SEARCH_F3_BQ_DATASTORE_ID",
        "structured": True,
    },
    {
        # F2 ver5.3 平坦テーブル（BigQuery）を索引する構造化データストア（§0-7 R3）
        "datastore_id": "nuro-f2-bq-knowledge",
        "display_name": "NuRO F2 Knowledge BQ (ver5.3平坦・BigQuery索引)",
        "env_key": "VERTEX_SEARCH_F2_BQ_DATASTORE_ID",
        "structured": True,
    },
]

# 検索エンジン（検索アプリ）。データストア単体でも servingConfig 直下検索は動くが、
# 推奨経路はエンジン経由（knowledge_loader._serving_config がエンジンIDを優先する）。
_ENGINES = [
    {
        "engine_id": "nuro-f2-bq-search",
        "display_name": "NuRO F2 BQ Search",
        "datastore_id": "nuro-f2-bq-knowledge",
        "env_key": "VERTEX_SEARCH_F2_BQ_ENGINE_ID",
    },
    {
        "engine_id": "nuro-f3-bq-search",
        "display_name": "NuRO F3 BQ Search",
        "datastore_id": "nuro-f3-bq-knowledge",
        "env_key": "VERTEX_SEARCH_F3_BQ_ENGINE_ID",
    },
]


def create_datastore(
    client: discoveryengine.DataStoreServiceClient,
    datastore_id: str,
    display_name: str,
    structured: bool = False,
) -> str:
    """データストアを作成して名前を返す。既存の場合はスキップ。

    structured=True は BigQuery 等の構造化データ用（content なし・struct フィールドを索引）。
    """
    parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/collections/default_collection"

    data_store = discoveryengine.DataStore(
        display_name=display_name,
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        content_config=(
            discoveryengine.DataStore.ContentConfig.NO_CONTENT
            if structured
            else discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED
        ),
        solution_types=[discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH],
    )

    try:
        operation = client.create_data_store(
            parent=parent,
            data_store=data_store,
            data_store_id=datastore_id,
        )
        print(f"  作成中... (datastore_id={datastore_id})")
        result = operation.result(timeout=120)
        print(f"  完了: {result.name}")
        return datastore_id
    except AlreadyExists:
        print(f"  既存のデータストアをスキップ: {datastore_id}")
        return datastore_id


def create_engine(
    client: discoveryengine.EngineServiceClient,
    engine_id: str,
    display_name: str,
    datastore_id: str,
) -> str:
    """検索エンジン（検索アプリ）を作成してIDを返す。既存の場合はスキップ。

    Args:
        client:       Discovery Engine の EngineServiceClient。
        engine_id:    作成するエンジンID。
        display_name: コンソールに表示される名前。
        datastore_id: エンジンに紐付けるデータストアID（作成済みであること）。

    Returns:
        作成（または既存確認）したエンジンID。
    """
    parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/collections/default_collection"

    engine = discoveryengine.Engine(
        display_name=display_name,
        solution_type=discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH,
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        data_store_ids=[datastore_id],
        search_engine_config=discoveryengine.Engine.SearchEngineConfig(
            search_tier=discoveryengine.SearchTier.SEARCH_TIER_STANDARD,
        ),
    )

    try:
        operation = client.create_engine(
            parent=parent,
            engine=engine,
            engine_id=engine_id,
        )
        print(f"  作成中... (engine_id={engine_id} → {datastore_id})")
        operation.result(timeout=300)
        print(f"  完了: {engine_id}")
        return engine_id
    except AlreadyExists:
        print(f"  既存のエンジンをスキップ: {engine_id}")
        return engine_id


def main() -> None:
    if not GCP_PROJECT_ID:
        print("エラー: GOOGLE_CLOUD_PROJECT が .env に設定されていません")
        sys.exit(1)

    print(f"プロジェクト: {GCP_PROJECT_ID} / ロケーション: {GCP_LOCATION}\n")

    client = discoveryengine.DataStoreServiceClient()
    created = []

    for spec in _DATASTORES:
        print(f"[{spec['datastore_id']}] {spec['display_name']}")
        datastore_id = create_datastore(
            client,
            spec["datastore_id"],
            spec["display_name"],
            structured=spec.get("structured", False),
        )
        created.append((spec["env_key"], datastore_id))
        time.sleep(1)

    # 検索エンジン（検索アプリ）。データストア作成後でないと紐付けできない
    engine_client = discoveryengine.EngineServiceClient()
    for spec in _ENGINES:
        print(f"[{spec['engine_id']}] {spec['display_name']}")
        engine_id = create_engine(
            engine_client,
            spec["engine_id"],
            spec["display_name"],
            spec["datastore_id"],
        )
        created.append((spec["env_key"], engine_id))
        time.sleep(1)

    print("\n✅ 完了。現行のBQ経路では以下を .env に設定してください:\n")
    # 無印キーにもBQデータストアIDを設定する（knowledge_loader が検索に使うのは無印キー。
    # 旧 nuro-f2-knowledge 等を設定すると直接投入時代の古いストアを検索してしまう）
    recommended = [
        ("VERTEX_SEARCH_F2_DATASTORE_ID", "nuro-f2-bq-knowledge"),
        ("VERTEX_SEARCH_F3_DATASTORE_ID", "nuro-f3-bq-knowledge"),
        ("VERTEX_SEARCH_F2_BQ_DATASTORE_ID", "nuro-f2-bq-knowledge"),
        ("VERTEX_SEARCH_F3_BQ_DATASTORE_ID", "nuro-f3-bq-knowledge"),
        ("VERTEX_SEARCH_F2_BQ_ENGINE_ID", "nuro-f2-bq-search"),
        ("VERTEX_SEARCH_F3_BQ_ENGINE_ID", "nuro-f3-bq-search"),
    ]
    for env_key, resource_id in recommended:
        print(f"  {env_key}={resource_id}")
    print("\n次のステップ: uv run python scripts/preliminary_review/ingest_knowledge.py --backend bigquery")


if __name__ == "__main__":
    main()
