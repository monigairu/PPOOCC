"""
Google Cloud Storage クライアント

アップロード・ダウンロード・署名付きURLの生成を担う。
サービスアカウントキーファイル（GOOGLE_APPLICATION_CREDENTIALS）を使用する。
"""
from __future__ import annotations

import datetime
import logging
import os
import re
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

_client: storage.Client | None = None
_credentials: service_account.Credentials | None = None


def _get_credentials() -> service_account.Credentials:
    global _credentials
    if _credentials is None:
        key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not key_path:
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS が設定されていません")
        _credentials = service_account.Credentials.from_service_account_file(key_path)
    return _credentials


def get_gcs_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client(credentials=_get_credentials())
    return _client


def sanitize_path_component(value: str) -> str:
    """GCSパスに使用する文字列を安全な形式に変換する"""
    return re.sub(r"[^\w\-]", "_", value)


def upload_bytes(
    bucket_name: str,
    blob_path: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """
    バイト列をGCSにアップロードし、gsパスを返す。

    Returns:
        gs://{bucket_name}/{blob_path}
    """
    client = get_gcs_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    gcs_path = f"gs://{bucket_name}/{blob_path}"
    logger.info("GCSにアップロード完了: %s", gcs_path)
    return gcs_path


def upload_file(
    bucket_name: str,
    blob_path: str,
    local_path: str | Path,
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
) -> str:
    """
    ローカルファイルをGCSにアップロードし、gsパスを返す。

    Returns:
        gs://{bucket_name}/{blob_path}
    """
    client = get_gcs_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(str(local_path), content_type=content_type)
    gcs_path = f"gs://{bucket_name}/{blob_path}"
    logger.info("GCSにアップロード完了: %s", gcs_path)
    return gcs_path


def generate_signed_url(
    bucket_name: str,
    blob_path: str,
    expiration_minutes: int = 15,
    filename: str | None = None,
) -> str:
    """
    GCSオブジェクトの署名付きURL（v4）を生成する。

    サービスアカウントキーで署名するため、ローカル・Cloud Run どちらでも動作する。

    Args:
        bucket_name:        GCSバケット名
        blob_path:          バケット内のオブジェクトパス
        expiration_minutes: URLの有効期限（分）
        filename:           Content-Dispositionに設定するファイル名

    Returns:
        署名付きURL（文字列）
    """
    credentials = _get_credentials()
    client = get_gcs_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    response_disposition = None
    if filename:
        safe_name = filename.replace('"', "")
        response_disposition = f'attachment; filename="{safe_name}"'

    url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=expiration_minutes),
        method="GET",
        credentials=credentials,
        response_disposition=response_disposition,
    )
    return url
