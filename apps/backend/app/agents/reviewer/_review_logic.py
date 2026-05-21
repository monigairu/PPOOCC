"""
レビュー用ヘルパー関数（循環インポート回避のため独立モジュールに切り出し）

依存関係:
  reviewer_agent.py  → (re-export のみ)
  adk/agents.py      → このモジュールから直接インポート

このモジュールは reviewer_agent.py も adk/ もインポートしない。
"""
from __future__ import annotations

import json
import logging
import re

from apps.backend.app.api.models import ReviewItem
from apps.backend.app.core.frame_config_loader import load_frame_config

logger = logging.getLogger(__name__)

# 計画・実績の差分を「大きい」と判断する閾値（数値フィールドのみ）
_NUMERIC_DIFF_THRESHOLD_RATE = 0.1  # 10%以上の差異を指摘対象とする


# ── 計画・実績差分 ────────────────────────────────────────────────────────────

def detect_plan_diff(
    mappings: list[dict],
    frame_name: str = "frameB",
    sheet_name: str = "MRC1",
) -> list[dict]:
    """
    同一 mappings 内の計画値（G列）と実績値（K列）を比較して差分を返す。

    「実績」提出の場合のみ比較する。
    「計画」提出の場合は空リストを返す（計画時は他の観点でレビュー）。
    """
    kubun_value = ""
    for m in mappings:
        if m.get("field_name") == "計画実績区分":
            kubun_value = str(m.get("value", "")).strip()
            break

    if kubun_value != "実績":
        return []

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

    cell_to_value = {m["cell_address"]: m.get("value", "") for m in mappings}

    diffs = []
    for field_name, cells in plan_actual_pairs.items():
        plan_cell   = cells["plan_cell"]
        actual_cell = cells["actual_cell"]
        plan_val    = cell_to_value.get(plan_cell, "")
        actual_val  = cell_to_value.get(actual_cell, "")

        if not plan_val and not actual_val:
            continue

        diff_note = _evaluate_diff(plan_val, actual_val)
        if diff_note:
            diffs.append({
                "field_name":   field_name,
                "plan_cell":    plan_cell,
                "actual_cell":  actual_cell,
                "plan_value":   plan_val,
                "actual_value": actual_val,
                "diff_note":    diff_note,
            })

    return diffs


def _evaluate_diff(plan_val: str, actual_val: str) -> str | None:
    if plan_val == actual_val:
        return None
    if not plan_val and actual_val:
        return "計画値が未記入ですが実績値が入力されています"
    if plan_val and not actual_val:
        return "計画値が入力されていますが実績値が未記入です"

    plan_num   = _to_number(plan_val)
    actual_num = _to_number(actual_val)
    if plan_num is not None and actual_num is not None and plan_num != 0:
        rate = abs(actual_num - plan_num) / abs(plan_num)
        if rate >= _NUMERIC_DIFF_THRESHOLD_RATE:
            pct = round(rate * 100, 1)
            return f"計画値（{plan_val}）と実績値（{actual_val}）の乖離が {pct}% です"
        return None

    if len(plan_val) > 20 or len(actual_val) > 20:
        return f"計画時の記載（{plan_val[:30]}…）と実績時の記載が異なります"
    return f"計画値「{plan_val}」と実績値「{actual_val}」が一致しません"


def _to_number(value: str) -> float | None:
    cleaned = re.sub(r"[,，千円円万]", "", value).strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# ── プレースホルダー検出 ───────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(
    r"^[〇○◯●□■△▲▽▼※〜～ー\-ー\s]+$"
    r"|^（?未定）?$|^（?未記入）?$|^（?記入）?$"
    r"|^TBD$|^TBA$",
    re.IGNORECASE,
)
_REPEATED_SYMBOL_RE    = re.compile(r"^([〇○◯●□■△▲※])\1+\s*$")
_EMBEDDED_PLACEHOLDER_RE = re.compile(r"[〇○◯]{2,}|[●□■△▲]{2,}")


def _is_placeholder_value(value: str) -> bool:
    v = value.strip()
    if not v:
        return False
    return bool(_REPEATED_SYMBOL_RE.match(v) or _PLACEHOLDER_RE.match(v))


def _has_embedded_placeholder(value: str) -> bool:
    return bool(_EMBEDDED_PLACEHOLDER_RE.search(value.strip()))


def _compute_cell_sets(mappings: list[dict]) -> tuple[set[str], dict[str, str]]:
    empty_cells:       set[str]        = set()
    placeholder_cells: dict[str, str]  = {}

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
    cell_to_field = {m.get("cell_address", ""): m.get("field_name", "") for m in mappings}
    items = []

    for addr, val in placeholder_cells.items():
        field_name = cell_to_field.get(addr, addr)
        short_val  = val if len(val) <= 30 else val[:30] + "…"
        items.append(ReviewItem(
            item_id="",
            field_name=field_name,
            cell_address=addr,
            severity="要確認",
            comment=f"「{short_val}」にプレースホルダーが残っています。正式な内容に修正してください。",
            evidence="AI判断（ナレッジ参照なし）：〇〇等の仮置き文字は正式提出書類として不適切",
            knowledge_source="AI知見",
        ))

    return items


# ── プロンプト構築・レスポンスパース ──────────────────────────────────────────

def _number_records(records: list[dict], prefix: str) -> tuple[str, list[dict]]:
    if not records:
        return "（なし）", []
    numbered = []
    for i, r in enumerate(records, 1):
        r2 = dict(r)
        r2["_ref"] = f"[{prefix}#{i}]"
        numbered.append(r2)
    return json.dumps(numbered, ensure_ascii=False, indent=2), numbered


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
    valid_cells = {m.get("cell_address", "") for m in mappings if m.get("cell_address")}

    if empty_cells is None or placeholder_cells is None:
        empty_cells, placeholder_cells = _compute_cell_sets(mappings)

    f2_text,  _ = _number_records(f2_knowledge,    "F2")
    f3o_text, _ = _number_records(f3_own,          "F3own")
    f3a_text, _ = _number_records(f3_all,          "F3all")
    sim_text, _ = _number_records(similar_work,    "SIM")
    sup_text, _ = _number_records(supplement_info, "SUP")

    mappings_text  = json.dumps(mappings,   ensure_ascii=False, indent=2)
    plan_diff_text = (
        json.dumps(plan_diffs, ensure_ascii=False, indent=2)
        if plan_diffs else "（なし。計画提出または差分なし）"
    )
    valid_cells_text       = ", ".join(sorted(valid_cells)) or "（なし）"
    empty_cells_text       = ", ".join(sorted(empty_cells)) or "（なし）"
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
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Geminiレスポンスのパースに失敗しました: %s", raw[:200])
        return []

    items = data.get("review_items", [])
    result = []
    for i, item in enumerate(items):
        result.append(ReviewItem(
            item_id=f"review_{i + 1:03d}",
            field_name=item.get("field_name", "不明"),
            cell_address=item.get("cell_address", ""),
            severity=item.get("severity", "AIからの指摘"),
            comment=item.get("comment", ""),
            evidence=item.get("evidence", ""),
            knowledge_source=item.get("knowledge_source", ""),
        ))
    return result
