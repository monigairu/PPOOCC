"""
レビューエージェント エントリーポイント

ADK 2.0 Workflow 経由でナレッジ収集（並列）→ ルール検出 → Gemini レビュー生成を実行する。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Workflow 構造:
  START ──fan-out──┬── f2_node         ──┐
                   ├── f3_own_node     ──┤
                   ├── f3_all_node     ──┼── join ── rule_check ── synthesis
                   ├── similar_node    ──┤
                   └── supplement_node ──┘

Phase 2 制約（knowledge_loader.py のコメント参照）:
① 同義語・表記ゆれ: Vertex AI Search のハイブリッド検索（BM25+ベクトル）で対応済み
② reactor_type フィルタ: F3 スキーマ拡張後に有効化予定
③ 補足資料の写真・図面: Phase 3 でマルチモーダル SubAgent として追加

Phase 3 追加時:
  adk/agents.py に multimodal_node を追加し
  adk/runner.py の parallel_nodes タプルに登録するだけ。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
外部 I/F は Phase 1 から変更なし:
  run_review() の引数・戻り値は同一
  API エンドポイント（review.py）への影響ゼロ
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

from langfuse import observe

from apps.backend.app.preliminary_review.workflow import state_keys as K
from apps.backend.app.preliminary_review.workflow.runner import run_workflow
from apps.backend.app.preliminary_review.review_logic import (
    detect_plan_diff,
    _evaluate_diff,
    _to_number,
    _compute_cell_sets,
    _generate_rule_based_items,
    _build_prompt,
    _parse_review_response,
    build_search_query,
)
from apps.backend.app.preliminary_review.knowledge.result_reader import (
    reconstruct_mappings_from_excel,
    derive_query_context,
)
from apps.backend.app.preliminary_review.models import ReviewItem
from apps.backend.app.core.frame_config_loader import list_frame_sheets

logger = logging.getLogger(__name__)

# テストコードが `from ...preliminary_review.agent import detect_plan_diff` でインポートしているため
# review_logic からの re-export を維持する（名前は変わらない）
__all__ = [
    "run_review",
    "review_workbook",
    "extract_summary",
    "detect_plan_diff",
    "_evaluate_diff",
    "_to_number",
]


@observe(name="review", capture_input=True, capture_output=True)
async def run_review(
    session_id: str,
    utility_name: str,
    mappings: list[dict],
    frame_name: str = "frameB",
    sheet_name: str = "MRC1",
    reactor_type: str | None = None,
    fee_type: str | None = None,
) -> tuple[list[ReviewItem], list[dict]]:
    """
    ADK Workflow 経由でレビューを実行する。

    PoC：caller_role="NuRO" 固定
    本番移行時：引数に caller_role を追加して run_workflow に渡すだけでよい

    Returns:
        (ReviewItem のリスト, retrieval_trace のリスト)
        retrieval_trace: 各Toolの検索クエリ・取得件数・代表ドキュメントIDを含む
    """
    if not reactor_type:
        reactor_type = _extract_field(mappings, "炉型")
    if not fee_type:
        fee_type = _extract_field(mappings, "対象費目1")

    # 検索クエリは申請自身の「費目＋工事件名」で広げる（資料非依存・観点語は入れない）。
    # 費目のみだと同じ工事の話題違い事例が surfacing しないため。reactor_type フィルタは別途維持。
    search_query = build_search_query(mappings, fallback=fee_type or "")

    state = await run_workflow(
        session_id=session_id,
        utility_name=utility_name,
        mappings=mappings,
        frame_name=frame_name,
        sheet_name=sheet_name,
        reactor_type=reactor_type,
        fee_type=search_query,
    )

    review_items    = [ReviewItem(**d) for d in state.get(K.REVIEW_ITEMS, [])]
    retrieval_trace = state.get(K.RETRIEVAL_TRACE, [])
    return review_items, retrieval_trace


async def review_workbook(
    excel_path: Path | str,
    frame_name: str = "frameB",
    sheet_names: list[str] | None = None,
    utility_name: str | None = None,
    context_sheet: str = "MRC1",
    session_id: str | None = None,
) -> dict:
    """転記結果Excelのワークブック全体（全シート）を一括でAIレビューする統括関数。

    シートごとに「Excel→mappings復元（result_reader）→ run_review()」を回す
    ワークブック単位の入口。任意の転記結果Excelをファイルパスだけで
    レビューできるようにする（特定ファイル・特定費目に依存しない）。
    Firestore への保存は行わない（保存は API 層 review.py の責務）。

    Args:
        excel_path: レビュー対象の転記結果Excelのパス（例: 転記済みの frameB 様式）。
        frame_name: 様式名。`config/{frame_name}/` のシート定義YAMLを参照する。
        sheet_names: レビューするシート名のリスト。None なら
            `config/{frame_name}/*.yaml` に定義された全シート（例: MRC1・MRC2）。
        utility_name: 電力会社名。None なら申請Excel内の「電力会社」フィールドから
            自動取得し、それも無ければ "不明電力" を使う（ナレッジの自社/他社フィルタに使用）。
        context_sheet: RAGクエリ文脈（費目・炉型・会社）を引く基本情報シート名。
            MRC2 のように費目を持たないシートでも、同一申請のこのシートから文脈を補完する。
        session_id: トレース用のセッションID。None なら "workbook-xxxxxxxx" を自動生成。

    Returns:
        ワークブック全体のレビュー結果を持つ辞書:
            - "utility_name" (str): 実際にナレッジ検索に使った電力会社名。
            - "query_context" (dict): 検索に使った文脈
              {"fee_type": 費目, "reactor_type": 炉型, "utility_name": 会社}。
            - "sheets" (dict): シート名 → シート別結果。各値は
              {"review_items": list[ReviewItem]（AI指摘）,
               "retrieval_trace": list[dict]（各Toolの検索クエリ・件数）,
               "mappings": list[dict]（復元した {field_name, cell_address, value, reasoning}）}。
            - "skipped_sheets" (list[str]): mappings を復元できずスキップしたシート名。

    Raises:
        ValueError: frame_name のシート定義が config に1つも無い場合。
        FileNotFoundError: excel_path が存在しない場合。
    """
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"転記結果Excelが見つかりません: {excel_path}")

    target_sheets = sheet_names or list_frame_sheets(frame_name)
    if not target_sheets:
        raise ValueError(f"様式定義が見つかりません: config/{frame_name}/")

    session_id = session_id or f"workbook-{uuid.uuid4().hex[:8]}"

    # RAGクエリ文脈（費目・炉型・会社）は申請単位＝基本情報シートから1回導出して全シートで共有
    query_ctx = derive_query_context(excel_path, frame_name, context_sheet, context_sheet)
    resolved_utility = utility_name or query_ctx.get("utility_name") or "不明電力"

    sheets: dict[str, dict] = {}
    skipped: list[str] = []
    for sheet_name in target_sheets:
        try:
            mappings = reconstruct_mappings_from_excel(excel_path, frame_name, sheet_name)
        except Exception as e:  # 様式定義なし・シート読込失敗など＝そのシートだけスキップ
            logger.warning("シート %s の mappings 復元に失敗、スキップ: %s", sheet_name, e)
            skipped.append(sheet_name)
            continue
        if not mappings:
            skipped.append(sheet_name)
            continue

        review_items, retrieval_trace = await run_review(
            session_id=session_id,
            utility_name=resolved_utility,
            mappings=mappings,
            frame_name=frame_name,
            sheet_name=sheet_name,
            reactor_type=query_ctx.get("reactor_type"),
            fee_type=query_ctx.get("fee_type"),
        )
        sheets[sheet_name] = {
            "review_items": review_items,
            "retrieval_trace": retrieval_trace,
            "mappings": mappings,
        }

    return {
        "utility_name": resolved_utility,
        "query_context": query_ctx,
        "sheets": sheets,
        "skipped_sheets": skipped,
    }


def _extract_field(mappings: list[dict], field_name: str) -> str | None:
    """mappings から指定フィールドの値を取り出す"""
    for m in mappings:
        if m.get("field_name") == field_name:
            val = str(m.get("value", "")).strip()
            return val if val else None
    return None


def extract_summary(raw: str) -> str:
    """Gemini レスポンスから summary を抽出する"""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
        return data.get("summary", "")
    except json.JSONDecodeError:
        return ""
