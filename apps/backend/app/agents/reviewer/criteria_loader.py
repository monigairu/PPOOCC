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


def load_required_entries(frame_name: str, sheet_name: str) -> dict:
    """レビュー観点YAMLから記載必須欄の宣言（空欄チェック用）を読み込む。

    決定論の空欄検出ルール（_generate_missing_entry_items）が対象とする項目を返す。
    LLMプロンプトには含めない（全項目をLLMに渡す方式は過検出のため不採用・RAG_VERIFICATION §1-20）。
    宣言はopt-in：載せた項目だけをチェックする（対象費目2・対象号炉2〜4等の任意欄を
    デフォルトで指摘しないため）。

    Args:
        frame_name: 様式名（例 "frameB"）。
        sheet_name: シート名（例 "MRC1"）。

    Returns:
        {"required_fields": [フィールド名...],
         "required_table_columns": {表セクション名: {"共通"/"計画"/"実績": [列名...]}}}。
        ファイル・宣言が無い場合は両方空（＝チェックしない）。
    """
    empty = {"required_fields": [], "required_table_columns": {}}
    path = _CRITERIA_DIR / f"{frame_name}_{sheet_name}.yaml"
    if not path.exists():
        return empty
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("レビュー観点ファイルの読み込みに失敗しました（%s）: %s", path, e)
        return empty
    return {
        "required_fields": list(data.get("required_fields") or []),
        "required_table_columns": dict(data.get("required_table_columns") or {}),
    }


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
        "審査の主軸は「過去ナレッジ（F2/F3）との照合」です。過去に NuRO が同種の費目・工事で",
        "求めた確認事項が本様式で満たされているかを最優先で検証し、該当する過去事例があれば",
        "必ずその参照番号を根拠に指摘してください。",
        "以下のレビュー観点チェックリストは「見落とし防止の確認リスト」です。チェックリストで",
        "気づいた問題も、まず F2/F3 に同種の要求が無いか探し、あればそれを根拠にしてください。",
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
        "- チェックリストは「何を確認するか」の一覧である。**指摘の根拠にはしない**。",
        "- 指摘を作る際は、まず提示された F2/F3 過去事例に同種の要求・確認が無いかを照合し、",
        "  あれば**必ずその参照番号（[F3own#N] 等）を evidence に引用**する（過去事例に基づく指摘が最も価値が高い）。",
        "  チェックリストID（RC〇〇）を evidence の根拠として書かない（分類の補助にとどめる）。",
        "- F2/F3 に該当する過去事例が無い場合のみ「AI判断（ナレッジ参照なし）」とする。",
        "- ナレッジに根拠がある場合は severity='AIからの指摘'、判断のみの場合は severity='要確認'",
        "- 法令条文・数値基準を独自に根拠として引用しない（ハルシネーション防止）",
        "- 同一セルへの重複指摘は禁止（1セル1指摘まで）",
    ]

    return "\n".join(lines)
