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

from langfuse import observe

from apps.backend.app.agents.reviewer.adk import state_keys as K
from apps.backend.app.agents.reviewer.adk.runner import run_workflow
from apps.backend.app.agents.reviewer._review_logic import (
    detect_plan_diff,
    _evaluate_diff,
    _to_number,
    _compute_cell_sets,
    _generate_rule_based_items,
    _build_prompt,
    _parse_review_response,
)
from apps.backend.app.api.models import ReviewItem

logger = logging.getLogger(__name__)

# テストコードが `from reviewer_agent import detect_plan_diff` でインポートしているため
# _review_logic からの re-export を維持する（名前は変わらない）
__all__ = [
    "run_review",
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

    state = await run_workflow(
        session_id=session_id,
        utility_name=utility_name,
        mappings=mappings,
        frame_name=frame_name,
        sheet_name=sheet_name,
        reactor_type=reactor_type,
        fee_type=fee_type,
    )

    review_items    = [ReviewItem(**d) for d in state.get(K.REVIEW_ITEMS, [])]
    retrieval_trace = state.get(K.RETRIEVAL_TRACE, [])
    return review_items, retrieval_trace


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
