"""
レビューエージェント（Agentic RAG 軽量版）

5つの観点（Tool）を固定順で実行し、結果をまとめて Gemini に渡して指摘を生成する。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
現在のRAG方式：構造化フィルタ型RAG（Phase 1）

【Phase 1の制約】
① 同義語・表記ゆれに対応できない
   「費用低減」と「コスト削減」は別単語として扱われる
   → Phase 2のハイブリッド検索（BM25+ベクトル）で解決予定

② reactor_type（炉型）の絞り込みが機能しない
   F3スキーマに reactor_type 列が未定義
   → Phase 2でスキーマを拡張して対応

③ 補足資料の写真・図面情報が使えない
   Tool5でテキストのみ取得。写真は has_images フラグのみ記録
   → Phase 3でGemini 2.0 Flashのマルチモーダル機能で対応

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 2（PoC後半予定）：
  - Vertex AI Search + ハイブリッド検索（BM25+ベクトル）+ Reranking
  - knowledge_loader の内部実装のみ差し替え（このファイルへの影響なし）
  - Tool の動的選択（AgentExecutor 化）の検討

Phase 3（本番運用後）：
  - Gemini 2.0 Flash/Pro のマルチモーダルで写真・図面を処理
  - Document AI で解体状況図（PPTX）の構造解析
  - Graph RAGの必要性を評価（データ蓄積後に判断）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Toolの追加方法：
    1. knowledge_loader.py に新しい読み込み関数を追加
    2. run_review() 内に「Tool N: ...」のブロックを追加
    3. _build_prompt() のコンテキスト組み立て部分に追記
    このファイルのインターフェース（run_review の引数・戻り値）は変えない
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from langfuse import observe

from apps.backend.app.agents.reviewer import knowledge_loader
from apps.backend.app.agents.reviewer.criteria_loader import build_system_instruction
from apps.backend.app.api.models import ReviewItem
from apps.backend.app.core.ai_client import call_gemini
from apps.backend.app.core.frame_config_loader import load_frame_config

logger = logging.getLogger(__name__)

# 計画・実績の差分を「大きい」と判断する閾値（数値フィールドのみ）
# 文字列フィールドは存在有無の差分のみチェック
_NUMERIC_DIFF_THRESHOLD_RATE = 0.1  # 10%以上の差異を指摘対象とする


def detect_plan_diff(
    mappings: list[dict],
    frame_name: str = "frameB",
    sheet_name: str = "MRC1",
) -> list[dict]:
    """
    同一 mappings 内の計画値（G列）と実績値（K列）を比較して差分を返す。

    「実績」提出の場合のみ比較する。
    「計画」提出の場合は空リストを返す（計画時は他の観点でレビュー）。

    Args:
        mappings:   upload.py が生成した CellMapping のリスト（dict形式）
        frame_name: 様式名
        sheet_name: シート名

    Returns:
        差分が大きいフィールドの情報リスト。各要素は以下の形式：
        {"field_name": ..., "plan_cell": ..., "actual_cell": ...,
         "plan_value": ..., "actual_value": ..., "diff_note": ...}
    """
    # 計画実績区分を確認（実績の場合のみ差分チェック）
    kubun_value = ""
    for m in mappings:
        if m.get("field_name") == "計画実績区分":
            kubun_value = str(m.get("value", "")).strip()
            break

    if kubun_value != "実績":
        return []

    # MRC1.yaml の plan_actual セクションから plan/actual ペアを取得
    try:
        config = load_frame_config(frame_name, sheet_name)
    except FileNotFoundError:
        logger.warning("様式定義が見つかりません: %s/%s", frame_name, sheet_name)
        return []

    plan_actual_pairs: dict[str, dict] = {}
    for section in config.get("sections", []):
        if section.get("type") != "plan_actual":
            continue
        for field_name, cell_info in section.get("fields", {}).items():
            if isinstance(cell_info, dict) and "plan" in cell_info and "actual" in cell_info:
                plan_actual_pairs[field_name] = {
                    "plan_cell": str(cell_info["plan"]),
                    "actual_cell": str(cell_info["actual"]),
                }

    # mappings を cell_address → value のマップに変換
    cell_to_value = {m["cell_address"]: m.get("value", "") for m in mappings}

    diffs = []
    for field_name, cells in plan_actual_pairs.items():
        plan_cell = cells["plan_cell"]
        actual_cell = cells["actual_cell"]
        plan_val = cell_to_value.get(plan_cell, "")
        actual_val = cell_to_value.get(actual_cell, "")

        if not plan_val and not actual_val:
            continue

        diff_note = _evaluate_diff(plan_val, actual_val)
        if diff_note:
            diffs.append({
                "field_name": field_name,
                "plan_cell": plan_cell,
                "actual_cell": actual_cell,
                "plan_value": plan_val,
                "actual_value": actual_val,
                "diff_note": diff_note,
            })

    return diffs


def _evaluate_diff(plan_val: str, actual_val: str) -> str | None:
    """
    計画値と実績値を比較し、指摘が必要な場合はその説明文を返す。
    差異なしの場合は None を返す。
    """
    if plan_val == actual_val:
        return None

    # 一方だけ空欄
    if not plan_val and actual_val:
        return "計画値が未記入ですが実績値が入力されています"
    if plan_val and not actual_val:
        return "計画値が入力されていますが実績値が未記入です"

    # 数値として比較
    plan_num = _to_number(plan_val)
    actual_num = _to_number(actual_val)
    if plan_num is not None and actual_num is not None and plan_num != 0:
        rate = abs(actual_num - plan_num) / abs(plan_num)
        if rate >= _NUMERIC_DIFF_THRESHOLD_RATE:
            pct = round(rate * 100, 1)
            return (
                f"計画値（{plan_val}）と実績値（{actual_val}）の乖離が {pct}% です"
            )
        return None  # 10%未満の差異は指摘しない

    # 文字列として比較（内容が違う）
    if len(plan_val) > 20 or len(actual_val) > 20:
        # 長文は差異があれば指摘
        return f"計画時の記載（{plan_val[:30]}…）と実績時の記載が異なります"
    return f"計画値「{plan_val}」と実績値「{actual_val}」が一致しません"


def _to_number(value: str) -> float | None:
    """文字列から数値を抽出する（カンマ・単位を除去）"""
    cleaned = re.sub(r"[,，千円円万]", "", value).strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


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
    5つのToolでナレッジ収集し、Geminiにレビューを依頼して指摘リストとtrace情報を返す。

    PoC：caller_role="NuRO" 固定
    本番移行時：引数に caller_role を追加して knowledge_loader に渡すだけでよい

    Returns:
        (ReviewItem のリスト, retrieval_trace のリスト)
        retrieval_trace: 各Toolの検索クエリ・取得件数・代表ドキュメントIDを含む
    """
    if not reactor_type:
        reactor_type = _extract_field(mappings, "炉型")
    if not fee_type:
        fee_type = _extract_field(mappings, "対象費目1")

    retrieval_trace: list[dict] = []

    # ── Tool 1: F2ナレッジ（NuRO内有の知見）────────────────────────────
    f2_knowledge = knowledge_loader.load_f2(
        caller_role="NuRO", fee_type=fee_type, limit=20,
    )
    retrieval_trace.append({
        "tool": "Tool1（F2ナレッジ）",
        "query": fee_type or "（クエリなし）",
        "count": len(f2_knowledge),
        "top_ids": [r.get("_doc_id", "") for r in f2_knowledge[:3]],
    })

    # ── Tool 2a: F3ナレッジ（自社）──────────────────────────────────────
    f3_own = knowledge_loader.load_f3(
        caller_role="NuRO", utility_name=utility_name,
        reactor_type=reactor_type, fee_type=fee_type, limit=20,
    )
    retrieval_trace.append({
        "tool": f"Tool2a（F3自社: {utility_name}）",
        "query": fee_type or "（クエリなし）",
        "count": len(f3_own),
        "top_ids": [r.get("_doc_id", "") for r in f3_own[:3]],
    })

    # ── Tool 2b: F3ナレッジ（他社）──────────────────────────────────────
    f3_all = knowledge_loader.load_f3(
        caller_role="NuRO", utility_name=None,
        reactor_type=reactor_type, fee_type=fee_type, limit=20,
    )
    retrieval_trace.append({
        "tool": "Tool2b（F3他社）",
        "query": fee_type or "（クエリなし）",
        "count": len(f3_all),
        "top_ids": [r.get("_doc_id", "") for r in f3_all[:3]],
    })

    # ── Tool 3: 類似工事データ（スタブ）────────────────────────────────
    similar_work = knowledge_loader.load_similar_work(
        caller_role="NuRO", reactor_type=reactor_type, fee_type=fee_type, limit=20,
    )
    retrieval_trace.append({
        "tool": "Tool3（類似工事データ）",
        "query": fee_type or "（クエリなし）",
        "count": len(similar_work),
        "top_ids": [],
        "note": "Phase2現在データ未入手",
    })

    # ── Tool 4: 補足資料（テキストのみ）────────────────────────────────
    supplement_info = knowledge_loader.load_supplement(
        caller_role="NuRO", utility_name=utility_name, fee_type=fee_type, limit=20,
    )
    retrieval_trace.append({
        "tool": "Tool4（補足資料）",
        "query": fee_type or "（クエリなし）",
        "count": len(supplement_info),
        "top_ids": [r.get("source_file", "") for r in supplement_info[:3]],
    })

    # ── Tool 5: 計画・実績差分（ルールベース）──────────────────────────
    plan_diffs = detect_plan_diff(mappings, frame_name=frame_name, sheet_name=sheet_name)
    retrieval_trace.append({
        "tool": "Tool5（計画・実績差分）",
        "query": "G列/K列数値比較",
        "count": len(plan_diffs),
        "top_ids": [d.get("cell_address", "") for d in plan_diffs[:3]],
    })

    # ── ルールベース検出（Geminiに依存しない・必ず検出） ──────────────
    empty_cells, placeholder_cells = _compute_cell_sets(mappings)
    rule_items = _generate_rule_based_items(mappings, placeholder_cells)
    rule_cells = {item.cell_address for item in rule_items}

    # ── Gemini でレビュー生成 ─────────────────────────────────────────
    prompt = _build_prompt(
        mappings=mappings,
        f2_knowledge=f2_knowledge,
        f3_own=f3_own,
        f3_all=f3_all,
        similar_work=similar_work,
        supplement_info=supplement_info,
        plan_diffs=plan_diffs,
        utility_name=utility_name,
        sheet_name=sheet_name,
        empty_cells=empty_cells,
        placeholder_cells=placeholder_cells,
    )

    # Gemini でレビュー生成（@observe デコレーターが自動的にトレース）
    system_instruction = build_system_instruction(frame_name, sheet_name)
    raw_response = call_gemini(prompt, system_instruction=system_instruction)
    gemini_items = _parse_review_response(raw_response)

    # ── ルールベースとGeminiをマージ ──────────────────────────────────
    # ルールベースが検出済みのセルはGeminiの指摘を除外（重複防止）
    # ルールベースを先頭に置き、Geminiの残りを後ろに追加してから連番付与
    filtered_gemini = [i for i in gemini_items if i.cell_address not in rule_cells]
    all_items = rule_items + filtered_gemini
    for idx, item in enumerate(all_items, 1):
        item.item_id = f"review_{idx:03d}"

    return all_items, retrieval_trace


def _extract_field(mappings: list[dict], field_name: str) -> str | None:
    """mappings から指定フィールドの値を取り出す"""
    for m in mappings:
        if m.get("field_name") == field_name:
            val = str(m.get("value", "")).strip()
            return val if val else None
    return None


def _number_records(records: list[dict], prefix: str) -> tuple[str, list[dict]]:
    """
    ナレッジレコードに [prefix#N] の番号を付与してプロンプト用テキストと
    番号付きレコードリストを返す。Gemini が evidence に番号を引用できるようにする。
    """
    if not records:
        return "（なし）", []
    numbered = []
    for i, r in enumerate(records, 1):
        r2 = dict(r)
        r2["_ref"] = f"[{prefix}#{i}]"
        numbered.append(r2)
    return json.dumps(numbered, ensure_ascii=False, indent=2), numbered


# 提出書類として不適切なプレースホルダー値のパターン
# 「〇〇」「○○」など記入漏れを示す仮置き文字、または実質空白と同等の値
_PLACEHOLDER_RE = re.compile(
    r"^[〇○◯●□■△▲▽▼※〜～ー\-ー\s]+$"      # 記号のみで構成（単一記号も含む）
    r"|^（?未定）?$|^（?未記入）?$|^（?記入）?$"  # 未定・未記入系
    r"|^TBD$|^TBA$",
    re.IGNORECASE,
)

# 「〇〇」のように同一記号が2文字以上連続するパターン（全体一致・最も典型的なプレースホルダー）
_REPEATED_SYMBOL_RE = re.compile(r"^([〇○◯●□■△▲※])\1+\s*$")

# テキスト内に「〇〇」「○○」が埋め込まれているパターン（部分一致）
# 例: 「〇〇のため廃棄物処理を…」「〇〇工事と装置を共用…」
_EMBEDDED_PLACEHOLDER_RE = re.compile(r"[〇○◯]{2,}|[●□■△▲]{2,}")


def _is_placeholder_value(value: str) -> bool:
    """セル値全体がプレースホルダーかどうかを判定する（完全一致）"""
    v = value.strip()
    if not v:
        return False
    return bool(_REPEATED_SYMBOL_RE.match(v) or _PLACEHOLDER_RE.match(v))


def _has_embedded_placeholder(value: str) -> bool:
    """テキスト内に〇〇等のプレースホルダーが埋め込まれているかを判定する（部分一致）"""
    return bool(_EMBEDDED_PLACEHOLDER_RE.search(value.strip()))


def _compute_cell_sets(mappings: list[dict]) -> tuple[set[str], dict[str, str]]:
    """
    mappings から「空値セル」と「プレースホルダーセル」を抽出する。

    Returns:
        empty_cells:       値が空・無内容のセル番地セット
        placeholder_cells: {セル番地: 値} — 〇〇等のプレースホルダー（全体・埋め込みの両方）
    """
    empty_cells: set[str] = set()
    placeholder_cells: dict[str, str] = {}

    for m in mappings:
        addr = m.get("cell_address", "")
        if not addr:
            continue
        val = str(m.get("value", "")).strip()

        if not val or val in ("なし", "該当なし", "N/A", "-", "—"):
            empty_cells.add(addr)
        elif _is_placeholder_value(val) or _has_embedded_placeholder(val):
            placeholder_cells[addr] = val

    return empty_cells, placeholder_cells


def _generate_rule_based_items(
    mappings: list[dict],
    placeholder_cells: dict[str, str],
) -> list[ReviewItem]:
    """
    ルールベースで必ず検出すべき指摘を生成する（Geminiに依存しない）。

    現在のルール：
    - プレースホルダー値（〇〇・テキスト内埋め込みを含む）が含まれるセル → 必ず指摘
    """
    cell_to_field = {m.get("cell_address", ""): m.get("field_name", "") for m in mappings}
    items = []

    for addr, val in placeholder_cells.items():
        field_name = cell_to_field.get(addr, addr)
        # 表示は30文字に切り詰める
        short_val = val if len(val) <= 30 else val[:30] + "…"
        items.append(ReviewItem(
            item_id="",  # run_review() で連番付与
            field_name=field_name,
            cell_address=addr,
            severity="要確認",
            comment=f"「{short_val}」にプレースホルダーが残っています。正式な内容に修正してください。",
            evidence="AI判断（ナレッジ参照なし）：〇〇等の仮置き文字は正式提出書類として不適切",
            knowledge_source="AI知見",
        ))

    return items


def _build_prompt(
    mappings: list[dict],
    f2_knowledge: list[dict],
    f3_own: list[dict],
    f3_all: list[dict],
    similar_work: list[dict],
    supplement_info: list[dict],
    plan_diffs: list[dict],
    utility_name: str,
    sheet_name: str,
    empty_cells: set[str] | None = None,
    placeholder_cells: dict[str, str] | None = None,
) -> str:
    # 転記済みフィールドのセル番地セットを構築（範囲外レビュー防止）
    valid_cells = {m.get("cell_address", "") for m in mappings if m.get("cell_address")}

    # 呼び出し元で事前計算済みの場合はそれを使う（再計算コストを省く）
    if empty_cells is None or placeholder_cells is None:
        empty_cells, placeholder_cells = _compute_cell_sets(mappings)

    # ナレッジに番号を付与
    f2_text,  _ = _number_records(f2_knowledge,  "F2")
    f3o_text, _ = _number_records(f3_own,        "F3own")
    f3a_text, _ = _number_records(f3_all,        "F3all")
    sim_text, _ = _number_records(similar_work,  "SIM")
    sup_text, _ = _number_records(supplement_info, "SUP")

    mappings_text  = json.dumps(mappings,   ensure_ascii=False, indent=2)
    plan_diff_text = (
        json.dumps(plan_diffs, ensure_ascii=False, indent=2)
        if plan_diffs else "（なし。計画提出または差分なし）"
    )
    valid_cells_text       = ", ".join(sorted(valid_cells))  or "（なし）"
    empty_cells_text       = ", ".join(sorted(empty_cells))  or "（なし）"
    placeholder_cells_text = (
        json.dumps(placeholder_cells, ensure_ascii=False, indent=2)
        if placeholder_cells else "（なし）"
    )

    return f"""あなたはNuRO（廃炉管理機構）の審査担当AIです。
電力会社（{utility_name}）が提出した{sheet_name}様式の転記結果をレビューしてください。

## レビュー対象の転記結果
{mappings_text}

### レビュー対象セル番地一覧（この範囲のみ指摘可能）
{valid_cells_text}

### 値が空・未記載のセル番地
{empty_cells_text}

### プレースホルダー値が含まれるセル（セル番地: 値）
{placeholder_cells_text}

---

## 参照ナレッジ

### Tool1: NuROナレッジ（F2）— 各レコードに [F2#N] の参照番号付き
{f2_text}

### Tool2a: F3ナレッジ（{utility_name} 自社事例）— 各レコードに [F3own#N] の参照番号付き
{f3o_text}

### Tool2b: F3ナレッジ（他社類似事例）— 各レコードに [F3all#N] の参照番号付き
{f3a_text}

### Tool3: 類似工事データ — 各レコードに [SIM#N] の参照番号付き
{sim_text}

### Tool4: 補足資料 — 各レコードに [SUP#N] の参照番号付き
{sup_text}

### Tool5: 計画・実績の差分（ルールベース検出済み）
{plan_diff_text}

---

## 指示

上記のナレッジと転記結果を照合し、以下の観点で指摘事項をリストアップしてください。

1. NuROナレッジ（Tool1）・過去指摘事例（Tool2a/2b）に照らして問題のある箇所
2. 計画値と実績値の乖離（Tool5 の plan_diffs のみを根拠にすること）
3. 補足資料との不整合（Tool4参照）
4. 類似工事との比較で懸念がある箇所（Tool3参照）
5. 提出書類として不適切な記載（プレースホルダー・空欄）

---

## フィールドのレビュールール（必ず守ること）

### プレースホルダー値の扱い（必ず指摘すること）
- 「プレースホルダー値が含まれるセル」に列挙されたセルは**必ず**指摘する。
- 「〇〇」「○○」等の仮置き文字は、電力会社から正式提出される書類として不適切。
- comment には「〇〇等のプレースホルダーが残っており、正式な記載が必要です」と明記する。
- knowledge_source は "AI知見"、severity は "要確認" とする。

### 空値フィールドの扱い
- 「値が空・未記載のセル番地」のセルは、そのフィールドが必要な場合のみ指摘する。
- 「計画実績区分」が「実績」の場合、計画値・実績値の両方の記載が必要。
  いずれかが空欄なら「実績報告であるため計画値・実績値の記載が必要」と指摘する。
- 空欄指摘には「なぜそのフィールドが必要か」の文脈的根拠を必ず記載する。

### 範囲外セルへの指摘禁止
- 「レビュー対象セル番地一覧」に含まれないセル番地は cell_address に使用しない。
- 転記結果に存在しないフィールド名は field_name に使用しない。

### 計画・実績の差分
- 数値の差分指摘は Tool5（plan_diffs）に差分が検出された場合のみ生成する。
- plan_diffs が「なし」の場合、数値の乖離について独自に指摘しない。
- G列/K列を独自に比較・推定して差分指摘することは禁止。

### 重複指摘の禁止
- 同一の cell_address に対して複数の指摘を生成しない（1セル1指摘まで）。

---

## AIのみで指摘する場合の制約（ハルシネーション防止）

### 指摘できる観点（ナレッジなしの場合）
- 提出書類として不適切な記載：プレースホルダー（〇〇等）、意味のない仮置き値
- 記載の具体性が不十分：「〜に努める」「適切に実施する」等の曖昧な表現のみ
- 論理的な不整合：計画・実績の記載が矛盾している
- 具体的な数値・根拠の欠如（例：費用低減策の金額根拠がない）

### 絶対に行ってはいけないこと
- 法令条文・通達番号・数値基準（「〇〇条」「〇〇%以内」等）を根拠にした指摘
- 「規制で定められている」「法令上必要」という表現
- ナレッジに記載のない事実を「過去に指摘された」と表現すること
- ナレッジ（なし）なのに knowledge_source を "F2" や "F3" と記載すること

### ナレッジ参照なしの場合の出力ルール
- severity: 必ず "要確認"
- evidence: 必ず "AI判断（ナレッジ参照なし）：" で始め、判断根拠を簡潔に記述
- knowledge_source: "AI知見"

---

## 出力形式（このJSONのみを返してください。前後に説明文を入れないでください）

{{
  "review_items": [
    {{
      "field_name": "フィールド名（転記結果のfield_nameと一致させること）",
      "cell_address": "セル番地（レビュー対象セル番地一覧から選択）",
      "severity": "要確認 または AIからの指摘",
      "comment": "指摘内容（具体的に。曖昧表現を使うなら転記値を引用すること）",
      "evidence": "根拠（ナレッジを参照した場合は [F2#1] 等の番号を明記。参照なしの場合は 'AI判断（ナレッジ参照なし）：〇〇のため'）",
      "knowledge_source": "F2 / F3 / 類似工事 / 補足資料 / 計画差分 / AI知見"
    }}
  ],
  "summary": "全体的なレビュー所見（2〜3文）"
}}

severity の使い分け：
  "要確認"      → NuROの判断が必要、またはナレッジ参照なしの指摘
  "AIからの指摘" → ナレッジに明確な根拠がある場合のみ

指摘がない場合は review_items を空リストにしてください。
"""


def _parse_review_response(raw: str) -> list[ReviewItem]:
    """
    Gemini のレスポンスを ReviewItem リストに変換する。
    JSONのパースに失敗した場合は空リストを返す。
    """
    # コードブロック記法（```json ... ```）を除去
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Geminiレスポンスのパースに失敗しました: %s", raw[:200])
        return []

    items = data.get("review_items", [])
    result = []
    for i, item in enumerate(items):
        result.append(
            ReviewItem(
                item_id=f"review_{i + 1:03d}",
                field_name=item.get("field_name", "不明"),
                cell_address=item.get("cell_address", ""),
                severity=item.get("severity", "AIからの指摘"),
                comment=item.get("comment", ""),
                evidence=item.get("evidence", ""),
                knowledge_source=item.get("knowledge_source", ""),
            )
        )
    return result


def extract_summary(raw: str) -> str:
    """Gemini レスポンスから summary を抽出する"""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
        return data.get("summary", "")
    except json.JSONDecodeError:
        return ""
