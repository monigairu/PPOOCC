"""
NuRO用 アノテーション出力スクリプト

現在のAIレビュー出力を、NuRO担当者が○×を付けやすい Markdown 一覧として書き出す。
- AI指摘一覧（指摘・根拠とNuRO評価欄）
- ゴールド網羅状況（想定すべき指摘にAIが触れたかを自動マーキング＝見逃し可視化）

ゴールド指摘は `data/review_eval/gold_expectations.yaml` の review_cases[].gold_findings から読む
（configを書き換えれば内容を差し替え可能・本スクリプトは不変）。

実行：
    uv run python scripts/review_annotation.py \
        --excel data/form_generation/output/転記結果_frameB_関東電力.xlsx --sheets MRC1,MRC2

出力：data/review_eval/annotations/{申請名}_{日時}.md（コミット対象。NuROが○×記入して返す）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

# Excel→mappings復元・クエリ文脈導出は result_reader が正本（verify_rag経由の間接importは廃止）
from apps.backend.app.agents.reviewer.result_reader import (
    reconstruct_mappings_from_excel,
    derive_query_context,
)
from apps.backend.app.agents.reviewer import reviewer_agent

GOLD_FILE = Path("data/review_eval/gold_expectations.yaml")
OUT_DIR = Path("data/review_eval/annotations")


def _run(excel: Path, frame: str, sheet: str) -> tuple[list[dict], dict]:
    mappings = reconstruct_mappings_from_excel(excel, frame, sheet)
    ctx = derive_query_context(excel, frame, sheet)
    items, _ = asyncio.run(
        reviewer_agent.run_review(
            session_id="annotation",
            utility_name=ctx.get("utility_name") or "不明電力",
            mappings=mappings,
            frame_name=frame,
            sheet_name=sheet,
            reactor_type=ctx.get("reactor_type"),
            fee_type=ctx.get("fee_type"),
        )
    )
    return [i.model_dump() for i in items], ctx


def _gold_for(spec: dict, excel: str, sheet: str) -> list[dict]:
    for c in spec.get("review_cases", []):
        if c.get("excel") == excel and c.get("sheet") == sheet:
            return c.get("gold_findings", []) or []
    return []


def _md_escape(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="NuRO用アノテーション出力")
    parser.add_argument("--excel", required=True)
    parser.add_argument("--frame", default="frameB")
    parser.add_argument("--sheets", default="MRC1,MRC2")
    parser.add_argument("--gold", default=str(GOLD_FILE))
    args = parser.parse_args()

    excel = Path(args.excel)
    sheets = [s.strip() for s in args.sheets.split(",") if s.strip()]
    spec = yaml.safe_load(Path(args.gold).read_text(encoding="utf-8"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"{excel.stem}_{ts}.md"

    lines: list[str] = [f"# 事前レビュー アノテーション: {excel.stem}", "", f"- 生成: {ts}",
                        f"- 入力: `{excel}`", ""]

    for sheet in sheets:
        items, ctx = _run(excel, args.frame, sheet)
        gold = _gold_for(spec, args.excel, sheet)
        blob = " ".join((it.get("field_name", "") + it.get("comment", "") + it.get("evidence", "")) for it in items)

        lines += [f"## シート {sheet}", ""]
        if sheet == "MRC1":
            lines += [
                f"- 会社: {ctx.get('utility_name')} / 炉型: {ctx.get('reactor_type')} / 費目: {ctx.get('fee_type')}",
                "",
            ]

        # AI指摘一覧
        lines += ["### AI指摘一覧（NuROが評価を記入）", "",
                  "| # | 対象 | severity | 指摘 | 根拠(source/evidence) | NuRO評価(○適切/×不要) | コメント |",
                  "|---|---|---|---|---|---|---|"]
        if not items:
            lines.append("| — | （指摘なし） | | | | | |")
        for i, it in enumerate(items, 1):
            src = f"{it.get('knowledge_source','')} {it.get('evidence','')}".strip()
            lines.append(
                f"| {i} | {_md_escape(it.get('field_name',''))} ({_md_escape(it.get('cell_address',''))}) "
                f"| {_md_escape(it.get('severity',''))} | {_md_escape(it.get('comment',''))} "
                f"| {_md_escape(src)[:90]} |  |  |"
            )
        lines.append("")

        # ゴールド網羅状況
        lines += ["### ゴールド網羅状況（想定指摘の見逃し確認）", "",
                  "| Gold | 期待観点 | 想定source | 区分 | AIが触れた? | NuRO要否(○必要/×不要) | コメント |",
                  "|---|---|---|---|---|---|---|"]
        for g in gold:
            kws = g.get("keywords", [])
            touched = any(kw in blob for kw in kws)
            mark = "✓ 触れた" if touched else "— 未検出"
            lines.append(
                f"| {g.get('id','')} | {_md_escape(g.get('target',''))} | {g.get('source','')} "
                f"| {g.get('note','')} | {mark} |  |  |"
            )
        lines.append("")
        # 見逃しサマリ
        missed = [g.get("id") for g in gold if not any(kw in blob for kw in g.get("keywords", []))]
        if missed:
            lines += [f"> 未検出のゴールド: {', '.join(missed)}（recall=見逃し候補。NuROが要否を判断）", ""]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 アノテーション出力: {out}")


if __name__ == "__main__":
    main()
