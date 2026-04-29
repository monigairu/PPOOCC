"""
SKILL.md 読み込みモジュール

エージェント用の SKILL.md ファイルを読み込み、
変数を埋め込んでプロンプト文字列を生成する。
"""
from pathlib import Path


def load_skill(skill_dir: Path, skill_name: str = "SKILL.md") -> str:
    """
    指定ディレクトリから SKILL.md を読み込んで文字列として返す。

    Args:
        skill_dir: SKILL.md が配置されているディレクトリのパス
        skill_name: SKILL ファイル名（デフォルト: "SKILL.md"）

    Returns:
        SKILL ファイルの中身（文字列）
    """
    skill_path = skill_dir / skill_name
    if not skill_path.exists():
        raise FileNotFoundError(f"SKILL ファイルが見つかりません: {skill_path}")
    return skill_path.read_text(encoding="utf-8")


def render_skill(skill_text: str, **variables) -> str:
    """
    SKILL 内のプレースホルダを変数で置換する。

    プレースホルダは {{変数名}} の形式で記述する。
    （JSON 例との混同を避けるため、二重波括弧形式を採用）

    Args:
        skill_text: SKILL の元テキスト
        **variables: プレースホルダに埋め込む変数（キーワード引数）

    Returns:
        変数が埋め込まれた文字列
    """
    result = skill_text
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value))
    return result