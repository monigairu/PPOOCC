"""
マッピングキャッシュ管理モジュール

セルマッピングのAI判定結果をキャッシュとして保存し、
同じテンプレートに対する再判定を回避する。

ハッシュ計算対象:
  - Excelテンプレートファイル
  - 様式定義YAMLファイル

どちらかが変わればキャッシュを自動破棄するため、
手動で rm する必要がなくなる。
"""
import hashlib
import json
from pathlib import Path


def get_template_hash(
    template_path: str,
    yaml_path: str | None = None,
) -> str:
    """
    テンプレートファイルと様式定義YAMLの複合ハッシュ値を計算する。

    ExcelテンプレートとYAMLの両方が変更検知の対象。
    どちらかが変わればハッシュが変わり、キャッシュが自動的に無効化される。

    Args:
        template_path: Excelテンプレートファイルのパス
        yaml_path: 様式定義YAMLファイルのパス（省略時はExcelのみ）

    Returns:
        MD5 ハッシュ値（16進数文字列）
    """
    hasher = hashlib.md5()

    # Excelテンプレートのハッシュ
    with open(template_path, "rb") as f:
        hasher.update(f.read())

    # YAMLファイルのハッシュ（指定された場合）
    if yaml_path:
        yaml_file = Path(yaml_path)
        if yaml_file.exists():
            with open(yaml_file, "rb") as f:
                hasher.update(f.read())

    return hasher.hexdigest()


def load_mapping_cache(
    cache_path: str,
    template_hash: str,
) -> dict[str, list[str]] | None:
    """
    キャッシュファイルからマッピングを読み込む。

    キャッシュが存在し、かつハッシュが一致する場合のみ返す。
    一致しない場合は None を返す（自動的に再判定される）。

    Args:
        cache_path: キャッシュファイルのパス
        template_hash: 現在のテンプレート+YAMLの複合ハッシュ値

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
        print("   ⚠️  テンプレートまたはYAMLが変更されました。キャッシュを破棄して再判定します")
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
        template_hash: テンプレート+YAMLの複合ハッシュ値
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