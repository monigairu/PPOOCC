"""
ADK 2.0 Workflow Runner

run_workflow() を呼ぶと：
  1. Workflow を組み立てて Runner を起動する
  2. 入力パラメータを state_delta で注入する
  3. 各 FunctionNode が並列/直列で実行される
  4. 最終 Session.state から review_items と retrieval_trace を返す

Step 2 現在：ノードはプレースホルダー（即リターン）。
Step 3-6 で実際の knowledge_loader・Gemini 呼び出しを実装する。

将来の拡張（Phase 3）：
  MultimodalNode を parallel_nodes タプルに追加するだけでよい。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.agents import Context
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import FunctionNode, JoinNode, START, Workflow
from google.genai import types

from apps.backend.app.preliminary_review.workflow import state_keys as K
from apps.backend.app.preliminary_review.workflow.nodes import (
    f2_knowledge_node,
    f3_own_knowledge_node,
    f3_all_knowledge_node,
    similar_work_node,
    supplement_node,
    rule_check_node,
    synthesis_node,
)

logger = logging.getLogger(__name__)

# ── セッションサービス（アプリ起動中に1インスタンスを使い回す） ──────────────
_session_service = InMemorySessionService()
_APP_NAME = "nuro_reviewer"

def _build_workflow(
    f2_func=f2_knowledge_node,
    f3_own_func=f3_own_knowledge_node,
    f3_all_func=f3_all_knowledge_node,
    supplement_func=supplement_node,
    rule_check_func=rule_check_node,
    synthesis_func=synthesis_node,
) -> Workflow:
    """
    Workflow を組み立てて返す。

    各 func 引数に実装済み関数を渡すことで段階的に差し替える（Step 3-6）。
    Phase 3 でマルチモーダル SubAgent を追加する場合は
      parallel_nodes タプルに node を追加するだけでよい。

    グラフ構造:
        START ──fan-out──┬── f2_node     ──┐
                         ├── f3_own_node ──┤
                         ├── f3_all_node ──┼── join_node ── rule_check_node ── synthesis_node
                         └── supp_node   ──┘
    """
    f2_node          = FunctionNode(func=f2_func,         name="f2_node")
    f3_own_node      = FunctionNode(func=f3_own_func,     name="f3_own_node")
    f3_all_node      = FunctionNode(func=f3_all_func,     name="f3_all_node")
    similar_node_fn  = FunctionNode(func=similar_work_node, name="similar_node")
    supp_node        = FunctionNode(func=supplement_func, name="supplement_node")
    join_node        = JoinNode(name="knowledge_join")
    rule_check_node  = FunctionNode(func=rule_check_func, name="rule_check_node")
    synthesis_node   = FunctionNode(func=synthesis_func,  name="synthesis_node")

    # Phase 3: ここに MultimodalNode を追加するだけ
    parallel_nodes = (f2_node, f3_own_node, f3_all_node, similar_node_fn, supp_node)

    return Workflow(
        name="review_workflow",
        edges=[
            (START, parallel_nodes),              # fan-out: 並列実行開始
            *[(n, join_node) for n in parallel_nodes],  # fan-in: 全完了後に join
            (join_node, rule_check_node),         # Tool5 + ルールベース検出
            (rule_check_node, synthesis_node),    # Gemini レビュー生成
        ],
    )


async def run_workflow(
    session_id: str,
    utility_name: str,
    mappings: list[dict],
    frame_name: str = "frameB",
    sheet_name: str = "MRC1",
    reactor_type: str | None = None,
    fee_type: str | None = None,
    # Step 4/5 で実装関数に差し替え（完了したものはデフォルトが実装済み）
    f2_func=f2_knowledge_node,
    f3_own_func=f3_own_knowledge_node,
    f3_all_func=f3_all_knowledge_node,
    supplement_func=supplement_node,
    rule_check_func=rule_check_node,
    synthesis_func=synthesis_node,
) -> dict[str, Any]:
    """
    Workflow を実行して最終 Session.state を返す。

    戻り値の dict は以下のキーを含む（state_keys.py 参照）：
      - review_items:    list[dict]
      - retrieval_trace: list[dict]
    """
    workflow = _build_workflow(
        f2_func=f2_func,
        f3_own_func=f3_own_func,
        f3_all_func=f3_all_func,
        supplement_func=supplement_func,
        rule_check_func=rule_check_func,
        synthesis_func=synthesis_func,
    )

    # 入力パラメータをセッション作成時に state として注入
    # state_delta は FunctionNode 実行後のイベントに適用されるため
    # 実行前に使えるパラメータは create_session の state= で渡す
    initial_state: dict[str, Any] = {
        K.MAPPINGS:     mappings,
        K.UTILITY_NAME: utility_name,
        K.FRAME_NAME:   frame_name,
        K.SHEET_NAME:   sheet_name,
        K.REACTOR_TYPE: reactor_type,
        K.FEE_TYPE:     fee_type,
    }

    # セッションを毎回新規作成（reviewer は stateless なリクエスト単位）
    session = await _session_service.create_session(
        app_name=_APP_NAME,
        user_id=session_id,
        state=initial_state,
    )

    runner = Runner(
        node=workflow,
        app_name=_APP_NAME,
        session_service=_session_service,
    )

    async for event in runner.run_async(
        user_id=session_id,
        session_id=session.id,
        new_message=types.Content(parts=[types.Part(text="start_review")]),
    ):
        # イベントはログのみ（必要に応じて Langfuse トレースを追加可能）
        if event.output is not None:
            logger.debug("workflow event: node=%s output_type=%s", event.author, type(event.output).__name__)

    # 最終 state を取得して返す
    final_session = await _session_service.get_session(
        app_name=_APP_NAME,
        user_id=session_id,
        session_id=session.id,
    )
    return dict(final_session.state)
