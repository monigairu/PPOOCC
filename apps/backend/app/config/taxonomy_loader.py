"""MRC1分類taxonomyをframe設定YAMLから読み出す小さなloader.

MRC1の分類候補は、旧rulebookではなく frameB/MRC1 のframe設定YAML（`config/frames/frameB/MRC1.yaml`、
後方互換で `config/frameB/MRC1.yaml` も探索）の `taxonomy` が正本。
ruleset interpreter や分類stageは、この関数を通じて「分類候補の一覧」を取得する。

ここでは分類そのものや候補の補完は行わない。YAMLから `taxonomy` keyを取り出して返すだけを担当する。
"""

from pathlib import Path
from typing import Any

import yaml

from apps.backend.app.config.paths import CONFIG_ROOT, FRAMES_CONFIG_ROOT


def _default_mrc1_path() -> Path:
    """`config/frames/frameB/MRC1.yaml` を正とし、`config/frameB/MRC1.yaml` を後方互換で探索する。"""
    for root in (FRAMES_CONFIG_ROOT, CONFIG_ROOT):
        candidate = root / "frameB" / "MRC1.yaml"
        if candidate.exists():
            return candidate
    return FRAMES_CONFIG_ROOT / "frameB" / "MRC1.yaml"


DEFAULT_MRC1_PATH = _default_mrc1_path()


def load_taxonomy(path: str | Path = DEFAULT_MRC1_PATH) -> list[str]:
    """frame設定YAMLから分類taxonomy配列を読み込む。

    MRC1機器行を「配管」「弁」「大型機器」などの標準カテゴリへ寄せる前に、許可ラベル一覧を取得する。
    既定では `frameB/MRC1.yaml` を読むが、テストや検証では別pathを渡せる。

    Args:
        path (str | Path): `taxonomy` keyを持つframe設定YAMLのpath。

    Returns:
        list[str]: 分類候補ラベルの配列。後続の分類処理ではこの配列外のラベルを正本として扱わない。

    Raises:
        FileNotFoundError: 指定pathが存在しない場合。
        yaml.YAMLError: YAMLの構文が壊れている場合。
        KeyError: YAMLに `taxonomy` keyが無い場合。

    Examples:
        >>> taxonomy = load_taxonomy()  # doctest: +SKIP
        >>> "配管" in taxonomy  # doctest: +SKIP
        True

    Note:
        現行実装は `taxonomy` の型検証をしない。設定YAML側の契約として `list[str]` を維持する。
    """
    with Path(path).open(encoding="utf-8") as file:
        data: dict[str, Any] = yaml.safe_load(file)
    return data["taxonomy"]