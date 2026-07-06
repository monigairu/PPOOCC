"""
ADK 2.0 Workflow ノード関数定義

各関数は runner.py の FunctionNode にラップされて Workflow 内で実行される。

設計方針：
  - knowledge_loader.py の I/F（引数・戻り値）は一切変更しない
  - 同期関数を asyncio.run_in_executor でスレッドプールに投げることで
    FunctionNode の並列実行による真の並列 I/O を実現する
  - 各ノードは ctx.state を読み書きするだけ（副作用なし・単体テスト容易）
  - エラーは空リストにフォールバックしてレビュー全体を止めない

ノード一覧（Step 3 実装）：
  並列グループ：
    f2_knowledge_node      Tool1: F2ナレッジ
    f3_own_knowledge_node  Tool2a: F3ナレッジ（自社）
    f3_all_knowledge_node  Tool2b: F3ナレッジ（他社）
    supplement_node        Tool4: 補足資料
    similar_work_node      Tool3: 類似工事データ（スタブ）
  直列グループ（Step 4/5 で実装）：
    rule_check_node        Tool5: 計画実績差分 + プレースホルダー検出
    synthesis_node         Gemini レビュー生成

Phase 3 追加時：
  multimodal_node を runner.py の parallel_nodes タプルに追加するだけ。
  このファイルに新規関数を追加して runner.py で登録する。
"""
from __future__ import annotations

import asyncio
import functools
import logging

from google.adk.agents import Context

from apps.backend.app.agents.reviewer import knowledge_loader
from apps.backend.app.agents.reviewer.adk import state_keys as K
from apps.backend.app.agents.reviewer._review_logic import (
    detect_plan_diff,
    _compute_cell_sets,
    _generate_rule_based_items,
    _generate_missing_entry_items,
    _build_prompt,
    _parse_review_response,
    apply_relevance_guard,
    reanchor_review_items,
    humanize_evidence_refs,
)
from apps.backend.app.agents.reviewer.criteria_loader import build_system_instruction, load_required_entries
from apps.backend.app.api.models import ReviewItem
from apps.backend.app.core.ai_client import call_gemini

logger = logging.getLogger(__name__)


def _top_scores(records: list[dict], n: int = 3) -> list[float]:
    """検索結果上位 n 件の Reranking スコア（`_rerank_score`）を丸めて返す。

    Reranking の効き具合と閾値決定の実測用に retrieval_trace へ載せる（デバッグ可視化）。
    スコアが無いレコード（Reranking 無効・API失敗）は空リストになる。

    Args:
        records: 検索結果レコードのリスト。
        n: 先頭から何件のスコアを載せるか。

    Returns:
        小数第3位に丸めたスコアのリスト（`_rerank_score` を持つ先頭 n 件分）。
    """
    return [round(float(r["_rerank_score"]), 3) for r in records[:n] if "_rerank_score" in r]


# ── Tool1: F2ナレッジ（NuRO内有の知見） ──────────────────────────────────────
async def f2_knowledge_node(ctx: Context) -> None:
    """
    F2ナレッジを Vertex AI Search で検索して state に書き込む。
    knowledge_loader.load_f2() は同期関数のため run_in_executor で並列化。
    """
    fee_type = ctx.state.get(K.FEE_TYPE)
    try:
        loop = asyncio.get_running_loop()
        result: list[dict] = await loop.run_in_executor(
            None, knowledge_loader.load_f2, "NuRO", fee_type, 20
        )
    except Exception:
        logger.exception("Tool1(F2) 検索エラー")
        result = []

    ctx.state[K.F2_KNOWLEDGE] = result
    ctx.state[K.TRACE_F2] = {
        "tool": "Tool1（F2ナレッジ）",
        "query": fee_type or "（クエリなし）",
        "count": len(result),
        "top_ids": [r.get("_doc_id", "") for r in result[:3]],
        "top_scores": _top_scores(result),
    }


# ── Tool2a: F3ナレッジ（自社） ─────────────────────────────────────────────────
async def f3_own_knowledge_node(ctx: Context) -> None:
    """
    F3ナレッジ（申請電力会社の自社事例）を Vertex AI Search で検索する。
    """
    utility_name: str | None = ctx.state.get(K.UTILITY_NAME)
    reactor_type: str | None = ctx.state.get(K.REACTOR_TYPE)
    fee_type: str | None     = ctx.state.get(K.FEE_TYPE)
    try:
        loop = asyncio.get_running_loop()
        result: list[dict] = await loop.run_in_executor(
            None, knowledge_loader.load_f3,
            "NuRO", utility_name, reactor_type, fee_type, None, 20,
        )
    except Exception:
        logger.exception("Tool2a(F3自社) 検索エラー")
        result = []

    ctx.state[K.F3_OWN] = result
    ctx.state[K.TRACE_F3_OWN] = {
        "tool": f"Tool2a（F3自社: {utility_name}）",
        "query": fee_type or "（クエリなし）",
        "count": len(result),
        "top_ids": [r.get("_doc_id", "") for r in result[:3]],
        "top_scores": _top_scores(result),
    }


# ── Tool2b: F3ナレッジ（他社） ─────────────────────────────────────────────────
async def f3_all_knowledge_node(ctx: Context) -> None:
    """
    F3ナレッジ（全社の類似事例）を Vertex AI Search で検索する。
    """
    reactor_type: str | None = ctx.state.get(K.REACTOR_TYPE)
    fee_type: str | None     = ctx.state.get(K.FEE_TYPE)
    try:
        loop = asyncio.get_running_loop()
        result: list[dict] = await loop.run_in_executor(
            None, knowledge_loader.load_f3,
            "NuRO", None, reactor_type, fee_type, None, 20,
        )
    except Exception:
        logger.exception("Tool2b(F3他社) 検索エラー")
        result = []

    ctx.state[K.F3_ALL] = result
    ctx.state[K.TRACE_F3_ALL] = {
        "tool": "Tool2b（F3他社）",
        "query": fee_type or "（クエリなし）",
        "count": len(result),
        "top_ids": [r.get("_doc_id", "") for r in result[:3]],
        "top_scores": _top_scores(result),
    }


# ── Tool3: 類似工事データ（スタブ） ───────────────────────────────────────────
async def similar_work_node(ctx: Context) -> None:
    """
    類似工事データを返す。Phase 2 現在はデータ未入手のためスタブ。
    データ入手後、knowledge_loader.load_similar_work() の内部実装を差し替える。
    このノード関数は変更不要。
    """
    reactor_type: str | None = ctx.state.get(K.REACTOR_TYPE)
    fee_type: str | None     = ctx.state.get(K.FEE_TYPE)
    try:
        loop = asyncio.get_running_loop()
        result: list[dict] = await loop.run_in_executor(
            None, knowledge_loader.load_similar_work,
            "NuRO", reactor_type, fee_type, 20,
        )
    except Exception:
        logger.exception("Tool3(類似工事) 検索エラー")
        result = []

    # result は Phase2 現在データ未入手のためスタブが空リストを返す。
    # state への書き込みは K.SIMILAR_WORK キー追加時（Phase3）に合わせて行う。
    ctx.state[K.TRACE_SIMILAR] = {
        "tool": "Tool3（類似工事データ）",
        "query": fee_type or "（クエリなし）",
        "count": len(result),
        "top_ids": [],
        "note": "Phase2現在データ未入手",
    }


# ── Tool4: 補足資料（テキスト） ────────────────────────────────────────────────
async def supplement_node(ctx: Context) -> None:
    """
    補足資料（Excel）のテキストを読み込んで state に書き込む。
    Phase 3 ではマルチモーダル対応のノード（multimodal_node）が並列に追加される。
    このノードはテキスト読込専用のまま残す。
    """
    utility_name: str | None = ctx.state.get(K.UTILITY_NAME)
    fee_type: str | None     = ctx.state.get(K.FEE_TYPE)
    try:
        loop = asyncio.get_running_loop()
        result: list[dict] = await loop.run_in_executor(
            None, knowledge_loader.load_supplement,
            "NuRO", utility_name, fee_type, 20,
        )
    except Exception:
        logger.exception("Tool4(補足資料) 読込エラー")
        result = []

    ctx.state[K.SUPPLEMENT_INFO] = result
    ctx.state[K.TRACE_SUPPLEMENT] = {
        "tool": "Tool4（補足資料）",
        "query": fee_type or "（クエリなし）",
        "count": len(result),
        "top_ids": [r.get("source_file", "") for r in result[:3]],
    }


# ── Tool5 + ルールベース検出（直列・JoinNode 後） ─────────────────────────────
def rule_check_node(ctx: Context) -> None:
    """
    2つの処理を直列で行う（どちらも CPU のみ・I/O なし）。

    ① Tool5: detect_plan_diff()
       G列（計画値）と K列（実績値）を比較して乖離を検出する。
       「実績」提出時のみ動作し、計画提出時は空リストを返す。

    ② ルールベース検出: _compute_cell_sets + _generate_rule_based_items
       〇〇等のプレースホルダーを含むセルを必ず指摘する。
       Gemini に依存しない確定的な検出（重複防止のため synthesis_node に渡す）。

    ③ 記載必須欄の空欄検出: load_required_entries + _generate_missing_entry_items
       criteria YAML の required 宣言（opt-in）×様式定義のセル解決で、空欄の必須項目に
       「記入してください」を指摘する。表はアクティブ行（転記済み行）×必須列で判定。

    rule_items は ReviewItem を dict 化したもの（ADK state は JSON 直列化可能な型のみ）。
    synthesis_node でルール検出済みセルを除外するために rule_cells も書き込む。
    """
    mappings:   list[dict] = ctx.state.get(K.MAPPINGS, [])
    frame_name: str        = ctx.state.get(K.FRAME_NAME, "frameB")
    sheet_name: str        = ctx.state.get(K.SHEET_NAME, "MRC1")

    # ① 計画・実績差分（ルールベース・数値比較）
    plan_diffs = detect_plan_diff(mappings, frame_name=frame_name, sheet_name=sheet_name)

    # ② プレースホルダー・空値検出
    empty_cells, placeholder_cells = _compute_cell_sets(mappings)
    rule_items_obj = _generate_rule_based_items(mappings, placeholder_cells)

    # ③ 記載必須欄の空欄検出（決定論・criteria YAML の required 宣言に基づく。
    #    LLMに空欄項目を渡す方式は過検出のため不採用＝§1-20。rule_cells 経由で
    #    同一セルへのLLM重複指摘も自動排除される）
    required = load_required_entries(frame_name, sheet_name)
    rule_items_obj += _generate_missing_entry_items(
        mappings, frame_name, sheet_name,
        required["required_fields"], required["required_table_columns"],
    )

    # ReviewItem → dict（ADK state に格納するため）
    rule_items = [item.model_dump() for item in rule_items_obj]
    # synthesis_node でルール済みセルを除外するためのセット
    rule_cells = [item["cell_address"] for item in rule_items]

    ctx.state[K.PLAN_DIFFS]        = plan_diffs
    ctx.state[K.RULE_ITEMS]        = rule_items
    ctx.state[K.RULE_CELLS]        = rule_cells
    ctx.state[K.EMPTY_CELLS]       = list(empty_cells)
    ctx.state[K.PLACEHOLDER_CELLS] = placeholder_cells


# ── SynthesisNode: プロンプト構築 + Gemini 呼び出し（直列・最終ノード） ────────
async def synthesis_node(ctx: Context) -> None:
    """
    並列ノードが収集した全ナレッジをもとに Gemini でレビューを生成する。

    処理フロー:
      1. state から全ナレッジ・ルール結果を取得
      2. _build_prompt() でプロンプトを構築（現行コードをそのまま再利用）
      3. call_gemini() を run_in_executor で非同期化（Langfuse @observe 継続）
      4. ルール検出済みセル（rule_cells）と Gemini 指摘を重複なしでマージ
      5. item_id 採番後、review_items と retrieval_trace を state に書き込む
    """
    # ── state から入力を取得 ────────────────────────────────────────────────
    mappings:          list[dict]      = ctx.state.get(K.MAPPINGS, [])
    utility_name:      str             = ctx.state.get(K.UTILITY_NAME, "")
    frame_name:        str             = ctx.state.get(K.FRAME_NAME, "frameB")
    sheet_name:        str             = ctx.state.get(K.SHEET_NAME, "MRC1")
    f2_knowledge:      list[dict]      = ctx.state.get(K.F2_KNOWLEDGE, [])
    f3_own:            list[dict]      = ctx.state.get(K.F3_OWN, [])
    f3_all:            list[dict]      = ctx.state.get(K.F3_ALL, [])
    supplement_info:   list[dict]      = ctx.state.get(K.SUPPLEMENT_INFO, [])
    plan_diffs:        list[dict]      = ctx.state.get(K.PLAN_DIFFS, [])
    rule_items_dicts:  list[dict]      = ctx.state.get(K.RULE_ITEMS, [])
    rule_cells:        set[str]        = set(ctx.state.get(K.RULE_CELLS, []))
    empty_cells:       set[str]        = set(ctx.state.get(K.EMPTY_CELLS, []))
    placeholder_cells: dict[str, str]  = ctx.state.get(K.PLACEHOLDER_CELLS, {})

    # ── プロンプト構築（現行 reviewer_agent._build_prompt をそのまま使用） ──
    prompt = _build_prompt(
        mappings=mappings,
        f2_knowledge=f2_knowledge,
        f3_own=f3_own,
        f3_all=f3_all,
        similar_work=[],          # Tool3 はスタブ
        supplement_info=supplement_info,
        plan_diffs=plan_diffs,
        utility_name=utility_name,
        sheet_name=sheet_name,
        empty_cells=empty_cells,
        placeholder_cells=placeholder_cells,
    )

    # ── Gemini 呼び出し（sync → run_in_executor でノンブロッキング化） ───────
    # call_gemini には Langfuse @observe が付いているためそのままトレースされる
    system_instruction = build_system_instruction(frame_name, sheet_name)
    loop = asyncio.get_running_loop()
    raw_response: str = await loop.run_in_executor(
        None,
        functools.partial(call_gemini, prompt, system_instruction=system_instruction),
    )

    # ── レスポンスをパース ──────────────────────────────────────────────────
    gemini_items: list[ReviewItem] = _parse_review_response(raw_response)

    # ── 誤grounding防止：本申請の費目に整合しない F2/F3 根拠は AI知見へ降格 ──
    gemini_items = apply_relevance_guard(
        gemini_items, mappings, f2_knowledge, f3_own, f3_all
    )

    # ── 番地補正：空欄項目への指摘は番地を推測しがちなので様式定義で是正 ──
    # （実施費用低減策の指摘が工事件名 C6 に誤爆する等。config一致時のみ補正・表等は温存）
    gemini_items = reanchor_review_items(gemini_items, frame_name, sheet_name, mappings)

    # ── 出典の可読化：参照番号（[F3own#N]）をシート名・メッセージIDの出典表記へ ──
    # （番号は検索結果の並び順でしかなくユーザーに伝わらない。ガードは参照番号の
    #   字面で判定するため、必ず apply_relevance_guard より後に置く）
    gemini_items = humanize_evidence_refs(gemini_items, f2_knowledge, f3_own, f3_all)

    # ── ルール検出済みセルを除外してマージ ─────────────────────────────────
    filtered_gemini = [i for i in gemini_items if i.cell_address not in rule_cells]
    rule_items_obj  = [ReviewItem(**d) for d in rule_items_dicts]
    all_items       = rule_items_obj + filtered_gemini

    for idx, item in enumerate(all_items, 1):
        item.item_id = f"review_{idx:03d}"

    ctx.state[K.REVIEW_ITEMS] = [item.model_dump() for item in all_items]

    # ── retrieval_trace を各並列ノードの trace から収集 ────────────────────
    # 順序を元の reviewer_agent.py に合わせる
    trace_key_order = [
        K.TRACE_F2,
        K.TRACE_F3_OWN,
        K.TRACE_F3_ALL,
        K.TRACE_SIMILAR,
        K.TRACE_SUPPLEMENT,
    ]
    retrieval_trace = [ctx.state[k] for k in trace_key_order if k in ctx.state]
    retrieval_trace.append({
        "tool": "Tool5（計画・実績差分）",
        "query": "G列/K列数値比較",
        "count": len(plan_diffs),
        "top_ids": [],
    })
    ctx.state[K.RETRIEVAL_TRACE] = retrieval_trace
