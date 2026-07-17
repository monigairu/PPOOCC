"""
問い合わせ機能 評価ハーネス（フェーズ4・REQUIREMENTS §8）

`data/inquiry_eval/qa_cases.yaml`（A群=答えあり・B群=答えなし）に対して2軸で評価する
（事前レビュー eval_review.py のマトリクス評価と同じ流儀）：

  軸① 検索精度   : load_f3 を pipeline と同一条件で呼び、正解レコード（expected_ids）の
                    順位・リランクスコア・recall@k を観測する。RAG精度改善の土台
                    （Agent Search ハイブリッド検索＋Ranking API の挙動を直接見る層）。
  軸② E2E 4指標  : ask()（①〜④の全ゲート）で REQUIREMENTS §8 の4指標を算出する。
                    誤答率（B群・最重要）>0% のみハードFAIL（exit=1）。
                    他3指標の目標値は仮置き・未合意（§9-1）のため WARN 表示に留める。

判定は決定論（D-18・LLM審査員なし）：
  - 正答（A群）   = answered かつ evidences の record_id が expected_ids と交差
                    （qa_cases.yaml の判定規約「いずれかが根拠に使われれば正」）
  - 引用正確性    = A群 answered の引用レコードのうち expected_ids に含まれる割合
                    （引用のでっち上げは D-2 のタグ突合で構造排除済み。ここでは
                    「正しいレコードを引けたか」の精度を測る）
  - 棄却（B群）   = abstained（棄却経路も判別：0件ショートカット=D-7／②十分性／④接地）
  - 誤答（B群）   = answered（コア命題 §0-4 の直接検証）

補助指標（閾値較正の根拠・DESIGN §5）：
  - ②④ゲート通過率・0件ショートカット率・answered の接地スコア一覧・応答時間。
  - ④の生スコア観測（閾値スイープ）はコード変更不要：
      INQUIRY_GROUNDING_THRESHOLD=0 uv run python scripts/inquiry/eval_inquiry.py
    で④ゲートを実質無効化し、全 answered 候補のスコア分布を観測できる。

実行：uv run python scripts/inquiry/eval_inquiry.py [--runs 3] [--case B-4] [--verbose] [--json PATH]
      （--json の出力は一時生成物。コミットしない）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml

from apps.backend.app.inquiry.config import INQUIRY_TOP_K
from apps.backend.app.inquiry.models import AskResult
from apps.backend.app.inquiry.pipeline import ask
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    load_f3,
    normalize_utility,
)

DEFAULT_CASES = Path("data/inquiry_eval/qa_cases.yaml")

# 目標値（REQUIREMENTS §8。数値は仮置き・要合意 §9-1 のため WARN 判定にのみ使う）
TARGET_CORRECT = 0.80    # 正答率（A群）
TARGET_CITATION = 0.90   # 引用正確性（A群）
TARGET_ABSTAIN = 0.90    # 棄却率（B群）

# 検証マトリクスの難易度ラベル（qa_cases.yaml の search_level / gen_level に対応。
# 報告スライド「検索精度×回答品質」のセル単位で集計する）
SEARCH_LEVELS = {
    1: "質問がナレッジと同じ語彙 → 正解が上位にヒット",
    2: "同義語・表記揺れで語彙が一致しない → それでもヒット",
    3: "ナレッジ件数が少ない話題 → 埋もれずヒット",
}
GEN_LEVELS = {
    1: "単一レコードで完結する質問に正しく回答",
    2: "複数レコードの統合が必要な質問に正しく回答",
    3: "類似ヒットの中から必要な情報のみ選別して回答",
    4: "正しいデータがヒットしない際に誤答を生成しない",
}


def _snip(text, width: int = 70) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return s if len(s) <= width else s[:width] + "…"


# ── 軸① 検索精度（load_f3 の生ヒット観測） ─────────────────────────────────

def run_retrieval(question: str, utility: str) -> list[dict]:
    """pipeline.ask() の①と同一条件で検索する（判定条件をずらさない）。"""
    return load_f3(
        caller_role="電力",
        utility_name=utility,
        fee_type=question,
        limit=INQUIRY_TOP_K,
        raise_on_error=True,
    )


def eval_retrieval_a(case: dict, hits: list[dict]) -> dict:
    """A群：正解レコードの順位（最良）と recall@k を観測する。"""
    expected = set(case["expected_ids"])
    ranks = [i + 1 for i, h in enumerate(hits) if h.get("id") in expected]
    return {
        "case": case["id"],
        "search_level": case.get("search_level"),
        "hits": len(hits),
        "best_rank": ranks[0] if ranks else None,   # 正解群の最良順位（None=圏外）
        "found": len({h.get("id") for h in hits} & expected),
        "expected": len(expected),
        "top_score": hits[0].get("_rerank_score") if hits else None,
    }


def eval_retrieval_b(case: dict, hits: list[dict], own_utility: str) -> dict:
    """B群：ヒット件数と自社フィルタ（他社レコード混入なし）を観測する。

    (i) は0件が期待値・(ii)(iii) はヒットしても②ゲートで棄却されるハードネガティブ。
    他社混入は (iii) の検出対象（D-12：自社フィルタ破損の検出器）。
    """
    foreign = [
        h.get("utility_name") for h in hits
        if h.get("utility_name") and h["utility_name"] != own_utility
    ]
    return {
        "case": case["id"],
        "subcategory": _subcategory(case),
        "hits": len(hits),
        "foreign_hits": foreign,  # 空であるべき（自社フィルタの検証）
    }


def _subcategory(case: dict) -> str:
    """B群 note 冒頭の (i)(ii)(iii) サブカテゴリ（D-12）を取り出す。"""
    note = case.get("note", "")
    for tag in ("(iii)", "(ii)", "(i)"):
        if note.startswith(tag):
            return tag
    return "(?)"


# ── 軸② E2E 4指標（ask() 実行と決定論判定・D-18） ──────────────────────────

def run_ask_case(case: dict, utility: str) -> dict:
    """1ケースを ask() で実行し、判定に必要な観測値へ平坦化する。"""
    t0 = time.perf_counter()
    result: AskResult = ask(case["question"], utility)
    elapsed = time.perf_counter() - t0

    cited = sorted({ev.record_id for ev in result.evidences})
    return {
        "case": case["id"],
        "status": result.status,
        "cited": cited,
        "grounding_score": result.grounding_score,
        "abstain_reason": result.abstain_reason,
        "failed_stage": result.failed_stage,
        "abstain_route": _abstain_route(result),
        "related_ids": sorted({ev.record_id for ev in result.related}),
        "elapsed_sec": round(elapsed, 1),
        "answer": result.answer,
    }


def _abstain_route(result: AskResult) -> str | None:
    """棄却経路の判別（閾値較正・D-7/②/④ の切り分け用）。"""
    if result.status != "abstained":
        return None
    if result.abstain_reason == "insufficient_context":
        return "0件即棄却(D-7)" if not result.related else "②十分性判定"
    if result.abstain_reason == "low_grounding":
        return "④接地検査"
    return f"gate_error({result.failed_stage})"


def judge_a(case: dict, obs: dict) -> tuple[bool, str]:
    """A群の正答判定：answered かつ引用が expected_ids と交差（qa_cases.yaml の規約）。"""
    if obs["status"] != "answered":
        return False, f"誤棄却（{obs['abstain_route']}）"
    matched = set(obs["cited"]) & set(case["expected_ids"])
    if not matched:
        return False, f"根拠不一致（引用={obs['cited']} 期待={case['expected_ids']}）"
    return True, f"正答（根拠 {sorted(matched)}・接地 {obs['grounding_score']:.2f}）"


def judge_b(obs: dict) -> tuple[bool, str]:
    """B群の棄却判定：abstained なら正（誤答=answered がコア命題違反）。"""
    if obs["status"] == "abstained":
        return True, f"棄却OK（{obs['abstain_route']}）"
    return False, f"誤答（引用={obs['cited']}・接地={obs['grounding_score']}）"


# ── 集計・表示 ───────────────────────────────────────────────────────────────

def _pct(num: int, den: int) -> str:
    return f"{num}/{den}={num / den * 100:.0f}%" if den else "—"


def show_verbose(case: dict, obs: dict) -> None:
    print(f"      質問: {_snip(case['question'])}")
    if obs["status"] == "answered":
        print(f"      回答: {_snip(obs['answer'], 90)}")
        print(f"      引用: {obs['cited']} 接地={obs['grounding_score']}")
    else:
        print(f"      棄却: {obs['abstain_route']} 近傍={obs['related_ids'] or '（なし）'}")
    if case.get("expected_answer_gist"):
        print(f"      期待の要旨: {_snip(case['expected_answer_gist'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="問い合わせ機能 評価ハーネス（REQUIREMENTS §8 の4指標）")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--runs", type=int, default=1, help="軸②の実行回数（A-4 等の run 間振れの計測用）")
    parser.add_argument("--case", help="ケースIDの部分一致で絞り込み（例: --case B-4 / --case A）")
    parser.add_argument("--verbose", "-v", action="store_true", help="回答文・引用・近傍ナレッジの実出力を表示")
    parser.add_argument("--json", help="機械可読レポートの出力先（一時生成物。コミットしない）")
    args = parser.parse_args()

    spec = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))
    utility = spec["utility"]
    own = normalize_utility(utility) or utility
    a_cases = [c for c in spec["a_cases"] if not args.case or args.case in c["id"]]
    b_cases = [c for c in spec["b_cases"] if not args.case or args.case in c["id"]]
    if not a_cases and not b_cases:
        print(f"--case '{args.case}' に一致するケースがありません")
        sys.exit(2)

    report: dict = {"utility": utility, "runs": args.runs, "retrieval": [], "e2e": []}

    # ── 軸① 検索精度 ──
    print(f"=== 問い合わせ評価（utility={utility} / TOP_K={INQUIRY_TOP_K} / runs={args.runs}）===")
    print("\n── 軸① 検索精度（load_f3 生ヒット・正解の順位とフィルタ検証） ──")
    for case in a_cases:
        r = eval_retrieval_a(case, run_retrieval(case["question"], utility))
        report["retrieval"].append(r)
        rank = f"最良{r['best_rank']}位" if r["best_rank"] else "圏外"
        score = f"{r['top_score']:.3f}" if r["top_score"] is not None else "—"
        print(f"  [{'PASS' if r['best_rank'] else 'FAIL'}] {r['case']}: {rank}"
              f"（正解 {r['found']}/{r['expected']} 件ヒット・{r['hits']}件中・TOP1スコア={score}）")
    for case in b_cases:
        r = eval_retrieval_b(case, run_retrieval(case["question"], utility), own)
        report["retrieval"].append(r)
        filt = "他社混入なし" if not r["foreign_hits"] else f"他社混入 {r['foreign_hits']}"
        print(f"  [{'PASS' if not r['foreign_hits'] else 'FAIL'}] {r['case']} {r['subcategory']}: "
              f"{r['hits']}件ヒット・{filt}")
    filter_ok = all(not r.get("foreign_hits") for r in report["retrieval"] if "foreign_hits" in r)

    # ── 軸② E2E ──
    a_correct = a_answered = 0
    cited_total = cited_matched = 0
    b_abstained = b_wrong = 0
    routes: Counter[str] = Counter()
    times: list[float] = []
    scores: list[tuple[str, float]] = []

    for run in range(1, args.runs + 1):
        print(f"\n── 軸② E2E 4指標（run {run}/{args.runs}） ──")
        for case in a_cases:
            obs = run_ask_case(case, utility)
            ok, detail = judge_a(case, obs)
            obs.update(run=run, group="A", ok=ok, gen_level=case.get("gen_level"))
            report["e2e"].append(obs)
            a_correct += ok
            times.append(obs["elapsed_sec"])
            if obs["status"] == "answered":
                a_answered += 1
                matched = set(obs["cited"]) & set(case["expected_ids"])
                cited_total += len(obs["cited"])
                cited_matched += len(matched)
                scores.append((obs["case"], obs["grounding_score"]))
            print(f"  [{'PASS' if ok else 'FAIL'}] {obs['case']} — {detail}（{obs['elapsed_sec']}s）")
            if args.verbose:
                show_verbose(case, obs)
        for case in b_cases:
            obs = run_ask_case(case, utility)
            ok, detail = judge_b(obs)
            obs.update(run=run, group="B", ok=ok, subcategory=_subcategory(case),
                       gen_level=4)  # B群＝「答えが無いとき誤答しない」のセル
            report["e2e"].append(obs)
            b_abstained += ok
            b_wrong += not ok
            times.append(obs["elapsed_sec"])
            if obs["abstain_route"]:
                routes[obs["abstain_route"]] += 1
            print(f"  [{'PASS' if ok else 'FAIL'}] {obs['case']} {obs['subcategory']} — "
                  f"{detail}（{obs['elapsed_sec']}s）")
            if args.verbose:
                show_verbose(case, obs)

    # ── 指標サマリ ──
    n_a, n_b = len(a_cases) * args.runs, len(b_cases) * args.runs
    wrong_rate_zero = (b_wrong == 0)
    print("\n=== REQUIREMENTS §8 4指標 ===")

    def _line(label: str, num: int, den: int, target: float | None, hard: bool = False) -> None:
        rate = num / den if den else None
        if rate is None:
            status = "—"
        elif hard:
            status = "PASS" if num == 0 else "FAIL"
        else:
            status = "PASS" if target is not None and rate >= target else "WARN(仮目標未達)"
        print(f"  [{status}] {label}: {_pct(num, den)}"
              + (f"（目標 {'≒0%' if hard else f'≥{target:.0%}'}）" if target is not None or hard else ""))

    _line("正答率（A群）", a_correct, n_a, TARGET_CORRECT)
    _line("引用正確性（A群・引用レコード単位）", cited_matched, cited_total, TARGET_CITATION)
    _line("棄却率（B群）", b_abstained, n_b, TARGET_ABSTAIN)
    _line("誤答率（B群・最重要）", b_wrong, n_b, None, hard=True)

    print("\n=== 補助指標（閾値較正の根拠・DESIGN §5） ===")
    print(f"  棄却経路の内訳: {dict(routes) or '（棄却なし）'}")
    print(f"  A群の誤棄却: {n_a - a_answered}/{n_a} 件（coverage の損失側）")
    if scores:
        vals = [s for _, s in scores]
        print(f"  接地スコア（answered）: min={min(vals):.2f} max={max(vals):.2f} "
              f"個別={[(c, round(s, 2)) for c, s in scores]}")
        print("  → ④閾値（INQUIRY_GROUNDING_THRESHOLD・現行0.6）の較正材料。"
              "生スコア分布は INQUIRY_GROUNDING_THRESHOLD=0 での再実行で観測可")
    if times:
        print(f"  応答時間: 平均 {sum(times) / len(times):.1f}s / 最大 {max(times):.1f}s（目標値は §9-6 未確定）")
    print(f"  自社フィルタ（軸①）: {'混入なし' if filter_ok else '他社レコード混入あり（FAIL）'}")

    # ── 検証マトリクス（報告スライドのセル単位・qa_cases.yaml の難易度タグで集計） ──
    print("\n=== 検証マトリクス（難易度別） ===")
    print("  軸① ナレッジ検索精度:")
    for lv, label in SEARCH_LEVELS.items():
        rows = [r for r in report["retrieval"] if r.get("search_level") == lv]
        if not rows:
            continue
        n_ok = sum(1 for r in rows if r["best_rank"])
        detail = " ".join(f"{r['case']}={'○' if r['best_rank'] else '×'}" for r in rows)
        print(f"    [{'PASS' if n_ok == len(rows) else 'FAIL'}] 難易度{lv}"
              f"（{label}）: {_pct(n_ok, len(rows))}  {detail}")
    print("  軸② 回答の品質:")
    for lv, label in GEN_LEVELS.items():
        rows = [o for o in report["e2e"] if o.get("gen_level") == lv]
        if not rows:
            continue
        n_ok = sum(1 for o in rows if o["ok"])
        detail = " ".join(f"{o['case']}={'○' if o['ok'] else '×'}" for o in rows)
        print(f"    [{'PASS' if n_ok == len(rows) else 'FAIL'}] 難易度{lv}"
              f"（{label}）: {_pct(n_ok, len(rows))}  {detail}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nレポート出力: {args.json}（コミットしない）")

    hard_fail = (not wrong_rate_zero) or (not filter_ok)
    print("\n" + ("ハードFAILなし（誤答0件・他社混入なし）" if not hard_fail
                  else "ハードFAILあり：誤答または他社混入（exit=1）"))
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
