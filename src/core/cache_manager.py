"""
マッピングキャッシュ管理モジュール

セルマッピングのAI判定結果をキャッシュとして保存し、
同じテンプレートに対する再判定を回避する。
"""
import hashlib
import json
from pathlib import Path


def get_template_hash(template_path: str) -> str:
    """
    テンプレートファイルの MD5 ハッシュ値を計算する。

    テンプレートが変更されたかどうかをハッシュで判定するために使用する。

    Args:
        template_path: テンプレートファイルのパス

    Returns:
        MD5 ハッシュ値（16進数文字列）
    """
    with open(template_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def load_mapping_cache(
    cache_path: str,
    template_hash: str,
) -> dict[str, list[str]] | None:
    """
    キャッシュファイルからマッピングを読み込む。

    キャッシュが存在し、かつテンプレートのハッシュが一致する場合のみ
    マッピングを返す。一致しない場合は None を返す。

    Args:
        cache_path: キャッシュファイルのパス
        template_hash: 現在のテンプレートのハッシュ値

    Returns:
        マッピング辞書、またはキャッシュ無効時は None
    """
    cache_file = Path(cache_path)
    if not cache_file.exists():
        return None

    with open(cache_file, "r", encoding="utf-8") as f:
        cache = json.load(f)

    # ハッシュが一致しない場合はキャッシュを無効とみなす
    if cache.get("template_hash") != template_hash:
        return None

    print(f"キャッシュからマッピングを読み込みました: {cache_path}")
    return cache.get("mappings")


def save_mapping_cache(
    cache_path: str,
    template_hash: str,
    mappings: dict[str, list[str]],
) -> None:
    """
    マッピング結果をキャッシュファイルに保存する。

    Args:
        cache_path: キャッシュファイルの保存先パス
        template_hash: テンプレートのハッシュ値
        mappings: 保存するマッピング辞書
    """
    cache_file = Path(cache_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    cache = {
        "template_hash": template_hash,
        "mappings": mappings,
    }

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"マッピングをキャッシュに保存しました: {cache_path}")