"""
事前レビュー ゴールド回帰ランナー（PoC検証マトリクス・難易度1〜4×2軸）

`data/review_eval/gold_expectations.yaml` の「期待される性質」に対して
①ナレッジ検索精度（retrieval_cases）と②LLMレビュー品質（review_cases）を照合する。

正解（NuROお手本）が未確定の間は YAML の provisional: true で WARN 止まり。
確定後に provisional: false へ変えるとハードな回帰ゲート（FAILでexit=1）になる。
ケース・期待値の追加変更は YAML 編集のみで完結し、本スクリプトは変更不要。

実行：uv run python scripts/preliminary_review/eval_review.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml

# Excel→mappings復元・クエリ文脈導出は result_reader が正本（verify_rag経由の間接importは廃止）
from apps.backend.app.preliminary_review.knowledge.result_reader import (
    reconstruct_mappings_from_excel,
    derive_query_context,
)
from apps.backend.app.preliminary_review import agent as reviewer_agent
from apps.backend.app.preliminary_review.knowledge import knowledge_loader

DEFAULT_EXPECT = Path("data/review_eval/gold_expectations.yaml")
_LAW_WORDS = ("法令", "条文", "規制", "通達")


# ── 実出力の表示（--verbose：判定には影響しない） ──────────────────────────
def _snip(text, width: int = 60) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return s if len(s) <= width else s[:width] + "…"


def show_retrieval_input(case: dict) -> None:
    """検索ケースの入力（クエリ・フィルタ・件数上限）と合格条件を表示する。"""
    print(
        f"      入力: tool={case.get('tool', 'f3_all')} クエリ=「{case.get('query', '')}」"
        f" 炉型フィルタ={case.get('reactor_type') or '（指定なし）'}"
        f" 会社フィルタ={case.get('utility') or '（指定なし）'}"
        f" 取得上限={case.get('limit', 20)}件"
    )
    print(f"      合格条件: {json.dumps(case.get('expect', {}), ensure_ascii=False)}")


def show_review_input(case: dict, mappings: list[dict], ctx: dict, source: str) -> None:
    """レビューケースの入力（レビュー対象・検索文脈・転記結果・合格条件）を表示する。

    合成データのケースは転記結果を全件、Excel由来のケースは件数＋先頭抜粋を表示する。
    """
    print(f"      入力（レビュー対象）: {source} / {case.get('frame', 'frameB')} / {case.get('sheet', 'MRC1')}")
    print(
        f"      検索文脈: 会社={ctx.get('utility_name') or '（不明）'}"
        f" 費目={ctx.get('fee_type') or '（不明）'}"
        f" 炉型={ctx.get('reactor_type') or '（不明）'}"
    )
    is_synthetic = "synthetic_mappings" in case
    shown = mappings if is_synthetic else mappings[:8]
    label = "転記結果（合成・全件）" if is_synthetic else f"転記結果: {len(mappings)}件のセル値。先頭抜粋"
    print(f"      {label}:")
    for m in shown:
        print(f"         {m.get('cell_address', ''):6} {m.get('field_name', '')} = {_snip(m.get('value'), 50)}")
    if not is_synthetic and len(mappings) > len(shown):
        print(f"         … 他 {len(mappings) - len(shown)} 件")
    print(f"      合格条件: {json.dumps(case.get('expect', {}), ensure_ascii=False)}")


def show_retrieval_hits(hits: list[dict], top_n: int = 5) -> None:
    """検索ヒットの中身（順位・rerankスコア・費目・炉型・会社・内容）を表示する。"""
    for rank, h in enumerate(hits[:top_n], 1):
        score = h.get("_rerank_score")
        score_s = f"{float(score):.3f}" if score is not None else "  —  "
        print(
            f"      {rank}位 score={score_s} 費目={h.get('fee_type', '') or '（なし）'} "
            f"炉型={h.get('reactor_type', '') or '—'} 会社={h.get('utility_name', '') or '—'} "
            f"id={h.get('_doc_id', '')}"
        )
        print(f"         内容: {_snip(h.get('message_content'))}")
    if len(hits) > top_n:
        print(f"      … 他 {len(hits) - top_n} 件")


def show_review_items(items: list[dict]) -> None:
    """レビュー指摘の全件（重要度・セル・根拠種別・指摘文・根拠）を表示する。"""
    if not items:
        print("      （指摘なし）")
    for i, it in enumerate(items, 1):
        print(
            f"      指摘{i} [{it.get('severity', '')}] {it.get('field_name', '')} "
            f"({it.get('cell_address', '')}) 根拠種別={it.get('knowledge_source', '')}"
        )
        print(f"         指摘: {_snip(it.get('comment'), 90)}")
        print(f"         根拠: {_snip(it.get('evidence'), 90)}")


# ── 軸① 検索精度 ───────────────────────────────────────────────────────────
def run_retrieval(case: dict) -> list[dict]:
    tool = case.get("tool", "f3_all")
    q = case.get("query", "")
    rt = case.get("reactor_type")
    util = case.get("utility")
    limit = case.get("limit", 20)
    if tool == "f2":
        return knowledge_loader.load_f2("NuRO", q, limit)
    if tool == "f3_own":
        return knowledge_loader.load_f3("NuRO", util, rt, q, None, limit)
    return knowledge_loader.load_f3("NuRO", None, rt, q, None, limit)


def check_retrieval(hits: list[dict], expect: dict) -> list[str]:
    fails: list[str] = []
    n = len(hits)
    if "min_hits" in expect and n < expect["min_hits"]:
        fails.append(f"ヒット {n} < min_hits {expect['min_hits']}")
    fee_any = expect.get("fee_any")
    if fee_any and not any(any(f in str(h.get("fee_type", "")) for f in fee_any) for h in hits):
        fails.append(f"fee_any {fee_any} を含むヒットが無い")
    ar = expect.get("all_reactor")
    if ar:
        bad = [h.get("reactor_type", "") for h in hits if str(h.get("reactor_type", "")) != ar]
        if bad:
            fails.append(f"all_reactor={ar} に反するヒット {len(bad)}件（炉型フィルタ漏れ）")
    return fails


# ── 軸② レビュー品質 ───────────────────────────────────────────────────────
def prepare_review_inputs(case: dict) -> tuple[list[dict], dict, str]:
    """レビューケースの入力（転記結果mappings・検索文脈・入力元の表記）を組み立てる。

    実行と証跡表示（show_review_input）で同一の入力を使うために分離。
    """
    frame = case.get("frame", "frameB")
    sheet = case.get("sheet", "MRC1")
    if "synthetic_mappings" in case:
        mappings = [dict(m, reasoning=m.get("reasoning", "")) for m in case["synthetic_mappings"]]
        ctx = case.get("query", {})
        source = "合成データ（synthetic_mappings・実Excelなし）"
    else:
        excel = Path(case["excel"])
        mappings = reconstruct_mappings_from_excel(excel, frame, sheet)
        ctx = derive_query_context(excel, frame, sheet)
        source = str(excel)
    return mappings, ctx, source


def run_review_case(case: dict, prepared: tuple[list[dict], dict, str] | None = None) -> list[dict]:
    frame = case.get("frame", "frameB")
    sheet = case.get("sheet", "MRC1")
    mappings, ctx, _ = prepared if prepared is not None else prepare_review_inputs(case)
    utility = ctx.get("utility_name") or "不明電力"
    items, _ = asyncio.run(
        reviewer_agent.run_review(
            session_id="eval-review",
            utility_name=utility,
            mappings=mappings,
            frame_name=frame,
            sheet_name=sheet,
            reactor_type=ctx.get("reactor_type"),
            fee_type=ctx.get("fee_type"),
        )
    )
    return [i.model_dump() for i in items]


def check_review(items: list[dict], expect: dict) -> list[str]:
    fails: list[str] = []
    n = len(items)
    blob = " ".join((it.get("field_name", "") + it.get("comment", "") + it.get("evidence", "")) for it in items)
    has_f3 = any("F3" in it.get("knowledge_source", "") for it in items)
    has_f2 = any("F2" in it.get("knowledge_source", "") for it in items)

    if "max_items" in expect and n > expect["max_items"]:
        fails.append(f"件数 {n} > max_items {expect['max_items']}（過検出）")
    if "min_items" in expect and n < expect["min_items"]:
        fails.append(f"件数 {n} < min_items {expect['min_items']}（見逃し）")
    if expect.get("must_have_f3_grounded") and not has_f3:
        fails.append("F3根拠の指摘が0件（grounding喪失）")
    if expect.get("forbid_f3_grounded") and has_f3:
        bad = [it for it in items if "F3" in it.get("knowledge_source", "")]
        fails.append(f"正解不在なのにF3根拠の指摘 {len(bad)}件（ハルシネーション）")
    # F2（NuRO内共有ナレッジ）根拠の指摘。ガード修正でF2も根拠採用できるため両向きを検証する
    if expect.get("must_have_f2_grounded") and not has_f2:
        fails.append("F2根拠の指摘が0件（grounding喪失）")
    if expect.get("forbid_f2_grounded") and has_f2:
        bad = [it for it in items if "F2" in it.get("knowledge_source", "")]
        fails.append(f"正解不在なのにF2根拠の指摘 {len(bad)}件（ハルシネーション）")
    if expect.get("forbid_law_basis"):
        bad = [it for it in items if any(w in (it.get("comment", "") + it.get("evidence", "")) for w in _LAW_WORDS)]
        if bad:
            fails.append(f"法令/規制根拠の指摘 {len(bad)}件")
    kws = expect.get("should_flag_any") or []
    if kws and not any(kw in blob for kw in kws):
        fails.append(f"should_flag_any のいずれにも触れていない: {kws}")
    for kw in expect.get("must_flag") or []:
        if kw not in blob:
            fails.append(f"must_flag 未到達: 「{kw}」")
    return fails


# ── 実行 ───────────────────────────────────────────────────────────────────
def _status(fails: list[str], provisional: bool) -> str:
    if not fails:
        return "PASS"
    return "WARN(暫定)" if provisional else "FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC検証マトリクス ゴールド回帰ランナー")
    parser.add_argument("--expect", default=str(DEFAULT_EXPECT))
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="各ケースの実出力（検索ヒットの中身・指摘の全文）を表示する（判定は不変）",
    )
    parser.add_argument(
        "--case",
        help="ケース名の部分一致で実行対象を絞る（例: --case 難1 / --case MRC2）。証跡を項目単位で取る用途",
    )
    args = parser.parse_args()

    spec = yaml.safe_load(Path(args.expect).read_text(encoding="utf-8"))
    hard_fail = False
    matrix: list[tuple] = []  # (axis, difficulty, name, status, count)

    # --case 指定時はケース名の部分一致で絞る（項目単位の証跡取得用。判定基準は不変）
    retrieval_cases = spec.get("retrieval_cases", [])
    review_cases = spec.get("review_cases", [])
    if args.case:
        retrieval_cases = [c for c in retrieval_cases if args.case in c["name"]]
        review_cases = [c for c in review_cases if args.case in c["name"]]
        if not retrieval_cases and not review_cases:
            print(f"--case '{args.case}' に一致するケースがありません。定義済みケース:")
            for c in spec.get("retrieval_cases", []) + spec.get("review_cases", []):
                print(f"  - {c['name']}")
            sys.exit(2)

    print(f"=== PoC検証マトリクス (version={spec.get('version')}) ===")

    print("\n── 軸① ナレッジ検索精度 ──")
    for case in retrieval_cases:
        hits = run_retrieval(case)
        fails = check_retrieval(hits, case.get("expect", {}))
        prov = case.get("provisional", False)
        st = _status(fails, prov)
        hard_fail = hard_fail or (bool(fails) and not prov)
        print(f"  [{st}] 難{case.get('difficulty','?')} {case['name']} — {len(hits)}件")
        for f in fails:
            print(f"      - {f}")
        if args.verbose:
            show_retrieval_input(case)
            show_retrieval_hits(hits)
        matrix.append(("①検索", case.get("difficulty"), case["name"], st, len(hits)))

    print("\n── 軸② LLMレビュー品質 ──")
    for case in review_cases:
        prepared = prepare_review_inputs(case)
        items = run_review_case(case, prepared)
        fails = check_review(items, case.get("expect", {}))
        prov = case.get("provisional", False)
        st = _status(fails, prov)
        hard_fail = hard_fail or (bool(fails) and not prov)
        print(f"  [{st}] 難{case.get('difficulty','?')} {case['name']} — 指摘{len(items)}件")
        for f in fails:
            print(f"      - {f}")
        if args.verbose:
            show_review_input(case, prepared[0], prepared[1], prepared[2])
            show_review_items(items)
        matrix.append(("②レビュー", case.get("difficulty"), case["name"], st, len(items)))

    print("\n=== マトリクス結果 ===")
    for axis, diff, name, st, cnt in sorted(matrix, key=lambda x: (x[0], x[1] or 0)):
        print(f"  {axis} 難{diff}: {st:10} {name} ({cnt})")
    print("\n" + ("ハードFAILなし" if not hard_fail else "確定ケースにFAILあり（exit=1）"))
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
