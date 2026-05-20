"""
F2/F3ナレッジを Vertex AI Search データストアへ投入するスクリプト

実行方法:
    uv run python scripts/ingest_knowledge.py           # F2・F3両方
    uv run python scripts/ingest_knowledge.py --target f2
    uv run python scripts/ingest_knowledge.py --target f3

ナレッジExcelを更新したときも再実行すれば最新化される（上書きインポート）。

ドキュメント構造:
    id          : "{frame}_{sheet}_{record_id}" （例: f3_kni_1g_01_001_01）
    content     : メッセージ本文（BM25+ベクトル検索の対象）
    struct_data : フィルタ用メタデータ
                    knowledge_type, utility_name, fee_type, sheet_name 等
"""
from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf import struct_pb2

from apps.backend.app.agents.reviewer._excel_reader import read_all_f2, read_all_f3
from apps.backend.app.core.settings import (
    GCP_LOCATION,
    GCP_PROJECT_ID,
    VERTEX_SEARCH_F2_DATASTORE_ID,
    VERTEX_SEARCH_F3_DATASTORE_ID,
)

# Vertex AI Search は1リクエストあたり最大100件
_BATCH_SIZE = 100


def _build_document(
    record: dict[str, Any],
    knowledge_type: str,
) -> discoveryengine.Document:
    """
    ナレッジレコードを Vertex AI Search のドキュメント形式に変換する。

    content （検索対象テキスト）にはメッセージ本文を入れる。
    struct_data にはフィルタ・権限制御に使うメタデータを入れる。
    """
    # ドキュメントIDは英数字・ハイフン・アンダースコアのみ使用可
    raw_id = record.get("message_id") or record.get("id") or "unknown"
    doc_id = f"{knowledge_type}_{raw_id}".replace(" ", "_").replace("/", "_")[:128]

    # 検索対象テキスト: message_content > text_content の優先順
    content_text = (
        record.get("message_content")
        or record.get("text_content")
        or record.get("title")
        or ""
    ).strip()

    # struct_data: フィルタ用メタデータ + 検索結果で返すコンテンツ
    # NOTE: content.raw_bytes は Search レスポンスに含まれないため
    #       message_content を struct_data にも格納する
    struct_fields: dict[str, Any] = {
        "knowledge_type":    knowledge_type.upper(),
        "utility_name":      record.get("utility_name", ""),
        "fee_type":          record.get("cost_category") or record.get("fee_type", ""),
        "sheet_name":        record.get("sheet_name", ""),
        "message_direction": record.get("message_direction", ""),
        "message_content":   content_text,
        # 本番の権限制御用（NuROのみ参照可フラグ）
        "caller_role_required": "NuRO" if knowledge_type.upper() == "F2" else "any",
    }

    # protobuf Struct に変換
    pb_struct = struct_pb2.Struct()
    for k, v in struct_fields.items():
        if v:
            pb_struct.fields[k].string_value = str(v)

    return discoveryengine.Document(
        id=doc_id,
        struct_data=pb_struct,
        content=discoveryengine.Document.Content(
            raw_bytes=content_text.encode("utf-8"),
            mime_type="text/plain",
        ),
    )


def _import_batch(
    client: discoveryengine.DocumentServiceClient,
    parent: str,
    documents: list[discoveryengine.Document],
) -> None:
    """ドキュメントのバッチをインポートする。"""
    inline_source = discoveryengine.ImportDocumentsRequest.InlineSource(
        documents=documents
    )
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        inline_source=inline_source,
        # INLINEソースは INCREMENTAL のみ対応（FULL は GCS/BigQuery 専用）
        # 再実行時は既存ドキュメントを上書きして最新化される
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
    )
    operation = client.import_documents(request=request)
    result = operation.result(timeout=300)
    if result.error_samples:
        for err in result.error_samples[:3]:
            print(f"    ⚠ インポートエラー: {err}")


def ingest(target: str) -> None:
    """F2またはF3のナレッジを Vertex AI Search にインポートする。"""
    if not GCP_PROJECT_ID:
        print("エラー: GOOGLE_CLOUD_PROJECT が設定されていません")
        sys.exit(1)

    client = discoveryengine.DocumentServiceClient()

    if target in ("f2", "all"):
        _ingest_f2(client)
    if target in ("f3", "all"):
        _ingest_f3(client)


def _ingest_f2(client: discoveryengine.DocumentServiceClient) -> None:
    if not VERTEX_SEARCH_F2_DATASTORE_ID:
        print("スキップ: VERTEX_SEARCH_F2_DATASTORE_ID が未設定")
        return

    print("── F2ナレッジ投入 ──────────────────────────────────────")
    # Excel から直接読み込む（knowledge_loader は検索バックエンドなので使わない）
    records = read_all_f2()
    print(f"  取得: {len(records)} 件")

    if not records:
        print("  投入するレコードがありません")
        return

    parent = (
        f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
        f"/collections/default_collection/dataStores/{VERTEX_SEARCH_F2_DATASTORE_ID}"
        f"/branches/default_branch"
    )
    docs = [_build_document(r, "f2") for r in records]
    _batch_import(client, parent, docs, label="F2")
    print(f"  ✅ F2: {len(docs)} 件投入完了")


def _ingest_f3(client: discoveryengine.DocumentServiceClient) -> None:
    if not VERTEX_SEARCH_F3_DATASTORE_ID:
        print("スキップ: VERTEX_SEARCH_F3_DATASTORE_ID が未設定")
        return

    print("── F3ナレッジ投入 ──────────────────────────────────────")
    # Excel から直接読み込む（knowledge_loader は検索バックエンドなので使わない）
    records = read_all_f3()
    print(f"  取得: {len(records)} 件")

    if not records:
        print("  投入するレコードがありません")
        return

    parent = (
        f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
        f"/collections/default_collection/dataStores/{VERTEX_SEARCH_F3_DATASTORE_ID}"
        f"/branches/default_branch"
    )
    docs = [_build_document(r, "f3") for r in records]
    _batch_import(client, parent, docs, label="F3")
    print(f"  ✅ F3: {len(docs)} 件投入完了")


def _batch_import(
    client: discoveryengine.DocumentServiceClient,
    parent: str,
    docs: list[discoveryengine.Document],
    label: str,
) -> None:
    """_BATCH_SIZE 件ずつに分けてインポートする。"""
    total = len(docs)
    for i in range(0, total, _BATCH_SIZE):
        batch = docs[i : i + _BATCH_SIZE]
        print(f"  バッチ {i // _BATCH_SIZE + 1}/{-(-total // _BATCH_SIZE)}: {len(batch)} 件")
        _import_batch(client, parent, batch)
        time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="F2/F3ナレッジを Vertex AI Search に投入")
    parser.add_argument(
        "--target",
        choices=["f2", "f3", "all"],
        default="all",
        help="投入対象 (default: all)",
    )
    args = parser.parse_args()
    ingest(args.target)


if __name__ == "__main__":
    main()
