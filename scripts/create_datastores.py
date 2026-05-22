"""
Vertex AI Search データストア作成スクリプト（一度だけ実行）

実行方法:
    uv run python scripts/create_datastores.py

作成されるデータストア:
    nuro-f2-knowledge         ... F2ナレッジ（NuRO内有の知見）
    nuro-f3-knowledge         ... F3ナレッジ（電力別の問合せ履歴）
    nuro-supplement-knowledge ... 補足資料キャプション（Phase 3）

完了後、出力されたデータストアIDを .env に追記してください:
    VERTEX_SEARCH_F2_DATASTORE_ID=nuro-f2-knowledge
    VERTEX_SEARCH_F3_DATASTORE_ID=nuro-f3-knowledge
    VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID=nuro-supplement-knowledge
    VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID=nuro-supplement-engine
"""
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

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
]


def create_datastore(
    client: discoveryengine.DataStoreServiceClient,
    datastore_id: str,
    display_name: str,
) -> str:
    """データストアを作成して名前を返す。既存の場合はスキップ。"""
    parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/collections/default_collection"

    data_store = discoveryengine.DataStore(
        display_name=display_name,
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        content_config=discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED,
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


def main() -> None:
    if not GCP_PROJECT_ID:
        print("エラー: GOOGLE_CLOUD_PROJECT が .env に設定されていません")
        sys.exit(1)

    print(f"プロジェクト: {GCP_PROJECT_ID} / ロケーション: {GCP_LOCATION}\n")

    client = discoveryengine.DataStoreServiceClient()
    created = []

    for spec in _DATASTORES:
        print(f"[{spec['datastore_id']}] {spec['display_name']}")
        datastore_id = create_datastore(client, spec["datastore_id"], spec["display_name"])
        created.append((spec["env_key"], datastore_id))
        time.sleep(1)

    print("\n✅ 完了。以下を .env に追記してください:\n")
    for env_key, datastore_id in created:
        print(f"  {env_key}={datastore_id}")


if __name__ == "__main__":
    main()
