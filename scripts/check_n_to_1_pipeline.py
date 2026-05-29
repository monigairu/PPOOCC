"""
N対1 転記パイプライン 手動確認スクリプト

使い方:
    PYTHONPATH=. uv run python scripts/check_n_to_1_pipeline.py \\
        --files data/見積書.pdf data/物量データ.xlsx data/工程表.xlsx \\
        --sheet MRC1 \\
        --frame frameB \\
        --output output/MRC1_result.xlsx

出力:
    - 各ファイルから抽出されたフィールド一覧（source_location 付き）
    - 競合が発生したフィールドと解決結果
    - skipped_cells（writable:false でスキップされたセル一覧）
    - 計算仕様の検証結果（Python 再計算値・Gemini 申告値・一致/不一致）
"""
import argparse
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加（PYTHONPATH=. で起動する前提だが念のため）
sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.backend.app.agents.data_extractor.mapper import map_to_schema_from_doc
from apps.backend.app.core.settings import OUTPUT_DIR, TEMPLATE_PATH
from apps.backend.app.merger.field_merger import merge_extractions
from apps.backend.app.pipelines.form_generation_pipeline import generate_form_from_dict
from apps.backend.app.readers.source_document import select_reader
from apps.backend.app.tools.formula_executor import execute_formula


def main() -> None:
    parser = argparse.ArgumentParser(description="N対1 転記パイプライン 手動確認スクリプト")
    parser.add_argument("--files", nargs="+", required=True, help="入力ファイルのパス（複数指定可）")
    parser.add_argument("--sheet", default="MRC1", help="転記先シート名（デフォルト: MRC1）")
    parser.add_argument("--frame", default="frameB", help="様式フレーム名（デフォルト: frameB）")
    parser.add_argument("--output", default=None, help="出力 Excel パス（省略時は output/ に自動生成）")
    args = parser.parse_args()

    output_path = args.output or str(OUTPUT_DIR / "MRC1_check_result.xlsx")

    print("=" * 60)
    print("N対1 転記パイプライン 手動確認")
    print("=" * 60)

    # ── STEP 1: Reader ────────────────────────────────────────────────────────
    print("\n【Reader】")
    source_docs = []
    for file_path in args.files:
        try:
            reader_fn = select_reader(file_path)
            doc = reader_fn(file_path)
            source_docs.append(doc)
            meta_str = _format_metadata(doc.metadata)
            print(f"  ✅ {Path(file_path).name:<25} → source_type={doc.source_type}, document_kind={doc.document_kind}{meta_str}")
        except Exception as e:
            print(f"  ❌ {Path(file_path).name} の読み込みに失敗: {e}")

    if not source_docs:
        print("\n読み込めたファイルがありません。終了します。")
        return

    # ── STEP 2: Gemini 抽出 ───────────────────────────────────────────────────
    print("\n【抽出結果】（ファイルごと）")
    extractions = []
    for doc in source_docs:
        print(f"\n  📄 {Path(doc.source_file).name}（{doc.document_kind}）")
        result = map_to_schema_from_doc(doc, sheet_name=args.sheet, frame_name=args.frame)

        extracted_data = result.get("extracted_data", {})
        field_metadata = result.get("field_metadata", {})
        formula_specs = result.get("formula_specs", [])

        for field_name, value in extracted_data.items():
            if value is None:
                continue
            meta = field_metadata.get(field_name, {})
            confidence = meta.get("confidence", "?")
            src_loc = meta.get("source_location")
            conf_str = _confidence_label(confidence)
            loc_str = f" ({src_loc})" if src_loc else ""
            print(f"    {conf_str} {field_name:<25} {str(value)[:40]}{loc_str}")

        print(f"    計算仕様: {len(formula_specs)} 件")

        extractions.append({
            "source_file": doc.source_file,
            "document_kind": doc.document_kind,
            "data": extracted_data,
            "_metadata": field_metadata,
            "formula_specs": formula_specs,
        })

    # ── STEP 3: 計算仕様の検証 ────────────────────────────────────────────────
    print("\n【計算仕様の検証】")
    formula_results = []
    has_formula = False
    for ext in extractions:
        for spec in ext.get("formula_specs", []):
            has_formula = True
            fr = execute_formula(spec)
            formula_results.append(fr)
            consistent_mark = "✅" if fr.is_consistent else "⚠️ "
            src_str = _format_source_location(fr.source_location)
            print(
                f"  {consistent_mark} {fr.formula_name}: "
                f"Python={fr.python_result:.4f} {fr.result_unit} "
                f"vs Gemini={fr.gemini_result:.4f} {fr.result_unit}"
                f"{'  → 一致' if fr.is_consistent else f'  → 不一致: {fr.discrepancy_note}'}"
            )
            if src_str:
                print(f"     抽出元: {src_str}")

    if not has_formula:
        print("  （計算仕様なし）")

    # ── STEP 4: N:1 マージ ────────────────────────────────────────────────────
    print("\n【マージ結果】")
    merged, field_conflicts = merge_extractions(extractions)

    # formula の conflicts を追加
    all_conflicts = list(field_conflicts)
    for fr in formula_results:
        if fr.needs_review:
            all_conflicts.append({
                "type": "formula_inconsistency",
                "formula_name": fr.formula_name,
                "python_result": fr.python_result,
                "gemini_result": fr.gemini_result,
                "note": fr.discrepancy_note,
                "source_location": fr.source_location,
            })

    if field_conflicts:
        print(f"  ⚠️  競合フィールド: {len(field_conflicts)} 件")
        for c in field_conflicts:
            print(f"     フィールド: {c['field']}")
            for cand in c["candidates"]:
                print(f"       - {cand['value']!r}  ← {cand['source']} ({cand['document_kind']})")
    else:
        print("  競合なし")

    print(f"\n  マージ済みフィールド数: {len(merged)}")

    # ── STEP 5: MRC1 書き込み ─────────────────────────────────────────────────
    print(f"\n【書き込み結果】")
    input_data = {k: v["value"] for k, v in merged.items()}
    source_metadata = {
        k: {"source_location": v.get("source_location"), "confidence": v.get("confidence")}
        for k, v in merged.items()
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        cell_mappings, processed_sheets = generate_form_from_dict(
            input_data=input_data,
            source_metadata=source_metadata,
            template_excel_path=str(TEMPLATE_PATH),
            result_excel_path=output_path,
            frame_name=args.frame,
            source_filename="（複数ファイル）",
        )

        skipped_cells = _collect_skipped_cells(args.frame, args.sheet)
        print(f"  skipped_cells: {skipped_cells}  ← writable:false のため")
        print(f"  書き込みセル数: {len(cell_mappings)}")
        print(f"  処理シート: {processed_sheets}")

    except Exception as e:
        print(f"  ❌ 書き込みエラー: {e}")
        raise

    # ── サマリー ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("【サマリー】")
    print(f"  入力ファイル数  : {len(source_docs)}")
    print(f"  抽出フィールド数: {len(merged)}")
    print(f"  競合           : {len(all_conflicts)} 件")
    print(f"  計算式検証     : {len(formula_results)} 件（うち要確認 {sum(1 for fr in formula_results if fr.needs_review)} 件）")
    print(f"  skipped_cells  : {_collect_skipped_cells(args.frame, args.sheet)}")
    print(f"  出力           : {output_path}")
    print("=" * 60)


# ── ヘルパー関数 ──────────────────────────────────────────────────────────────

def _format_metadata(metadata: dict) -> str:
    parts = []
    if "sheets" in metadata:
        parts.append(f"{len(metadata['sheets'])} シート")
    if "processed_pages" in metadata:
        parts.append(f"{metadata['processed_pages']} ページ")
    return f", {', '.join(parts)}" if parts else ""


def _confidence_label(confidence) -> str:
    try:
        val = float(confidence)
        if val >= 0.8:
            return "✅"
        if val >= 0.5:
            return "⚠️ "
        return "❌"
    except (TypeError, ValueError):
        return "❓"


def _format_source_location(loc: dict | None) -> str:
    if not loc:
        return ""
    parts = []
    if "file" in loc:
        parts.append(loc["file"])
    if "sheet" in loc:
        parts.append(f"シート:{loc['sheet']}")
    if "row" in loc:
        parts.append(f"行{loc['row']}")
    if "page" in loc:
        parts.append(f"ページ{loc['page']}")
    return " / ".join(parts)


def _collect_skipped_cells(frame: str, sheet: str) -> list[str]:
    try:
        from apps.backend.app.core.frame_config_loader import load_frame_config
        config = load_frame_config(frame, sheet)
        schema = config.get("extraction_schema", {})
        return [name for name, defn in schema.items() if not defn.get("writable", True)]
    except FileNotFoundError:
        return []


if __name__ == "__main__":
    main()
