"""
Firestore 共通クライアント

ai_client.py と同じ遅延初期化パターンを採用する。
インポート時には接続せず、初回呼び出し時に初期化する。
"""
from google.cloud import firestore

_client: firestore.Client | None = None


def get_firestore_client() -> firestore.Client:
    global _client
    if _client is None:
        # GOOGLE_CLOUD_PROJECT 環境変数から自動的にプロジェクトを取得する
        _client = firestore.Client()
    return _client
