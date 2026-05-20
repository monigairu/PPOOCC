"""
レビュー観点ローダー

data/review_criteria/{frame}_{sheet}.yaml を読み込み、
Gemini の system_instruction として注入するテキストを生成する。

設計方針:
  - status=active の観点のみをプロンプトに含める（draft は含めない）
  - フレーム・シートごとにYAMLを分ける（将来の様式追加に対応）
  - このファイルのI/Fは変えない。観点の追加・変更はYAMLのみで完結する。
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CRITERIA_DIR = Path("data/review_criteria")


def load_criteria(frame_name: str, sheet_name: str) -> list[dict]:
    """
    指定フレーム・シートのレビュー観点を読み込む。

    Returns:
        status=active の観点辞書のリスト。ファイルが存在しない場合は空リスト。
    """
    path = _CRITERIA_DIR / f"{frame_name}_{sheet_name}.yaml"
    if not path.exists():
        logger.debug("レビュー観点ファイルが見つかりません: %s", path)
        return []

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        all_criteria = data.get("criteria", [])
        active = [c for c in all_criteria if c.get("status", "active") == "active"]
        logger.info("レビュー観点をロード: %s 件（合計 %s 件中）", len(active), len(all_criteria))
        return active
    except Exception as e:
        logger.warning("レビュー観点ファイルの読み込みに失敗しました（%s）: %s", path, e)
        return []


def build_system_instruction(frame_name: str, sheet_name: str) -> str:
    """
    レビュー観点YAMLから Gemini の system_instruction テキストを生成する。

    観点が0件の場合は空文字列を返す（呼び出し元でデフォルト指示文にフォールバックする）。
    """
    criteria = load_criteria(frame_name, sheet_name)
    if not criteria:
        return ""

    lines = [
        "あなたはNuRO（廃炉管理機構）の審査担当AIです。",
        "以下のレビュー観点チェックリストに従って転記結果を審査してください。",
        "チェックリストの各項目を必ず確認し、該当する問題があれば指摘してください。",
        "",
        "## レビュー観点チェックリスト",
        "",
    ]

    for c in criteria:
        cid = c.get("id", "")
        category = c.get("category", "")
        field = c.get("field") or "全フィールド"
        check = c.get("check", "").strip()
        severity = c.get("severity", "要確認")
        guidance = c.get("guidance", "").strip()

        lines.append(f"[{cid}] {category} — 対象: {field}")
        lines.append(f"  チェック内容: {check}")
        lines.append(f"  severity: {severity}")
        if guidance:
            lines.append(f"  指摘の方向性: {guidance}")
        lines.append("")

    lines += [
        "## 全般的な審査方針",
        "",
        "- 上記チェックリストに加え、ナレッジ・類似事例との照合も行う",
        "- ナレッジに根拠がある場合は severity='AIからの指摘'、判断のみの場合は severity='要確認'",
        "- 法令条文・数値基準を独自に根拠として引用しない（ハルシネーション防止）",
        "- 同一セルへの重複指摘は禁止（1セル1指摘まで）",
    ]

    return "\n".join(lines)
