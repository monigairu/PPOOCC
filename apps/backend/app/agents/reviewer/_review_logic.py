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

def _number_records(records: list[dict], prefix: str) -> str:
    if not records:
        return "（なし）"
    numbered = []
    for i, r in enumerate(records, 1):
        r2 = dict(r)
        r2["_ref"] = f"[{prefix}#{i}]"
        numbered.append(r2)
    return json.dumps(numbered, ensure_ascii=False, indent=2)


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

    f2_text  = _number_records(f2_knowledge,    "F2")
    f3o_text = _number_records(f3_own,          "F3own")
    f3a_text = _number_records(f3_all,          "F3all")
    sim_text = _number_records(similar_work,    "SIM")
    sup_text = _number_records(supplement_info, "SUP")

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

    # 関連性ゲート用：本申請の費目・工事（過去ナレッジを根拠採用してよいかの判断材料）
    def _field(name: str) -> str:
        return next((str(m.get("value", "")).strip() for m in mappings
                     if m.get("field_name") == name and str(m.get("value", "")).strip()), "")
    fee_for_review  = _field("対象費目1") or _field("対象費目2")
    work_for_review = _field("工事件名") or _field("件名")

    return f"""あなたはNuRO（廃炉管理機構）の審査担当AIです。
電力会社（{utility_name}）が提出した{sheet_name}様式の転記結果をレビューしてください。

## 本申請の対象（過去ナレッジの関連性判断に使う）
- 費目: {fee_for_review or "（不明）"}
- 工事件名: {work_for_review or "（不明）"}

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

### 最重要：過去ナレッジ（F2/F3）を根拠にした指摘を最優先で出すこと
- Tool2a/2b（F3）の各レコードは、**NuROが過去に同種の費目・工事で実際に求めた確認事項**である。
  例：「解体範囲の確認資料を提出してください」=過去に解体費の審査で範囲資料を要求した事実。
- 手順：F3/F2レコードを1件ずつ見て、「この過去事例が求めた事項が、本様式の転記結果に
  **記載されているか／十分か**」を照合する。
  - 記載が無い・不足・曖昧なら、その `[F3all#N]` / `[F3own#N]` / `[F2#N]` を evidence に明記し、
    `knowledge_source` を `F3`（または `F2`）、`severity` を `AIからの指摘` として指摘する。
  - comment は「過去に同種案件で〇〇の確認を求めた事例（[F3all#N]）があるが、本様式では
    △△が確認できない」のように、**過去事例と本様式のどこが噛み合っていないか**を具体的に書く。
- F3/F2に該当事例が無い箇所のみ、下記「AIのみで指摘する場合」に進む。
- 検索で提示されたF2/F3は実在のナレッジである。**これを根拠にすることはハルシネーションではない。**

### 【関連性ゲート】F2/F3を根拠採用する前に必ず確認すること（最優先・誤grounding防止）
- 検索は関連が薄いレコードも返すことがある。**根拠に使う前に、その過去事例が「本申請の対象」
  （上記の費目・工事件名）と同種かを必ず判定する。**
- 費目・工事・対象設備が**明らかに異なる**過去事例は、検索でヒットしていても **根拠にしない**。
  例：本申請の費目が「放射線管理費」なのに、検索結果が「解体撤去費／解体工事」の事例 → 不採用。
- 同義語・表記ゆれは同種とみなす（例：解体撤去費＝解体費＝施設解体費）。号機違い・系統違いでも
  同じ費目・同種工事なら参考にしてよい（その旨を明記）。
- 本申請に整合する過去事例が無い場合は、**knowledge_source=F2/F3 の指摘を作らない**。
  無理に根拠化せず、AI知見（プレースホルダー・曖昧表現など客観的に確認できるもの）に限るか、指摘なしとする。

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

### 重複・類似指摘の統合（指摘を増やしすぎない）
このレビューは NuRO 担当者の確認工数を削減する前段スクリーニングである。
出しすぎは棄却の手間を増やし価値を損なうため、必ず統合する。
- 同一の cell_address に対して複数の指摘を生成しない（1セル1指摘まで）。
- **同じ観点・同じ根拠（同じ evidence）の指摘が複数のセルにまたがる場合は、1件に統合する。**
  代表セルを cell_address に置き、comment 冒頭に対象セルを列挙する
  （例:「対象: J30, J31, …, J37 — いずれも単価の根拠が不明確」）。
  例: 「単価根拠が不明」を J30〜J37 で個別に8件出すのではなく、統合して1件にする。
- 似た趣旨の指摘は文面を変えただけで水増ししない。観点が同じなら1件にまとめる。

---

## AIのみで指摘する場合の制約（ハルシネーション防止）

※ この節は **F2/F3に該当事例が無い場合のみ** 適用する。
  検索で提示されたF2/F3レコードを根拠にした指摘（knowledge_source=F2/F3）は
  ハルシネーションではなく、むしろ最優先で出すべきもの。下記の禁止事項とは無関係。

### 指摘できる観点（ナレッジなしの場合）
ナレッジ根拠のない指摘は**客観的に確認できる確度の高いものに限る**（推測・主観で増やさない）。
- 提出書類として不適切な記載：プレースホルダー（〇〇等）、意味のない仮置き値
- 記載の具体性が不十分：「〜に努める」「適切に実施する」等の曖昧な表現のみ
- 論理的な不整合：計画・実績の記載が矛盾している
- 具体的な数値・根拠の欠如（例：費用低減策の金額根拠がない）

確度が低い・主観的な指摘（「より詳しく書いた方がよい」程度の改善提案）は**出さない**。
迷う場合は、根拠あり（F2/F3）の指摘を優先し、根拠なしの指摘は出さない方に倒す。

### 絶対に行ってはいけないこと
- 法令条文・通達番号・数値基準（「〇〇条」「〇〇%以内」等）を根拠にした指摘
- 「規制で定められている」「法令上必要」という表現
- ナレッジに記載のない事実を「過去に指摘された」と表現すること
- 提示されていないF2/F3レコードを捏造して引用すること、または該当レコードが無いのに
  knowledge_source を "F2" / "F3" と記載すること
  （※ 提示済みのF2/F3 [F2#N]/[F3all#N] 等を正しく引用する分には何ら問題ない）

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


def build_search_query(mappings: list[dict], fallback: str = "") -> str:
    """F3/F2 検索クエリを申請自身のフィールドから組み立てる（資料非依存・観点語をハードコードしない）。

    費目のみだと、ノイジーな費目文字列ではVertexが関連度で打ち切り、同じ工事の話題違い事例
    （工期・費用低減策 等）が検索に出てこない。費目＋工事件名でクエリを広げ、同一工事の過去事例を
    surfacing しやすくする。観点語（低減策・工期 等）は入れない（過剰適合になるため）。
    """
    def _f(name: str) -> str:
        return next((str(m.get("value", "")).strip() for m in mappings
                     if m.get("field_name") == name and str(m.get("value", "")).strip()), "")
    fee = _f("対象費目1") or _f("対象費目2")
    work = _f("工事件名") or _f("件名")
    q = " ".join(x for x in (fee, work) if x).strip()
    return q or fallback


def _fee_tokens(fee: str) -> set[str]:
    """費目から「費/料/金」等を除いた連続2文字トークン集合を作る（語の重なり判定用）。"""
    s = re.sub(r"[費料金（）()・\s]", "", str(fee))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def _fee_related(submission_fee: str, doc_fee: str) -> bool:
    """本申請の費目と過去事例の費目が同種か（語の重なりで判定）。

    同義語・表記ゆれは語が重なるため許容（施設解体一解体費 ⇔ 解体撤去費 は「解体」を共有）。
    全く別費目は語が重ならない（放射線管理費 ⇔ 解体撤去費）。ハードコードしない一般則。
    """
    if not submission_fee or not doc_fee:
        return False
    return bool(_fee_tokens(submission_fee) & _fee_tokens(doc_fee))


def apply_relevance_guard(
    items: list[ReviewItem],
    mappings: list[dict],
    f2_knowledge: list[dict],
    f3_own: list[dict],
    f3_all: list[dict],
) -> list[ReviewItem]:
    """誤grounding防止（本番堅牢化・難易度4対策）。

    検索は費目が違っても"近い"docを返すため、LLMが無関係な過去事例を根拠化することがある。
    引用した [F2#N]/[F3own#N]/[F3all#N] の費目が本申請の費目と語を共有しない場合、
    その grounding は不当とみなし AI知見へ降格する（指摘自体は残し、誤った根拠ラベルだけ外す）。
    プロンプトの意味判断を補う確定的な安全網。特定費目をハードコードしない。
    """
    submission_fee = next(
        (str(m.get("value", "")).strip() for m in mappings
         if m.get("field_name") in ("対象費目1", "対象費目2") and str(m.get("value", "")).strip()),
        "",
    )
    if not submission_fee:
        return items

    ref_fee: dict[str, str] = {}
    for prefix, recs in (("F2", f2_knowledge), ("F3own", f3_own), ("F3all", f3_all)):
        for i, r in enumerate(recs, 1):
            ref_fee[f"{prefix}#{i}"] = r.get("fee_type", "")

    for it in items:
        src = it.knowledge_source or ""
        if "F3" not in src and "F2" not in src:
            continue
        refs = re.findall(r"(F2|F3own|F3all)#(\d+)", it.evidence or "")
        cited_fees = [ref_fee.get(f"{p}#{n}", "") for p, n in refs]
        # 引用が本申請の費目に整合しない（または引用が辿れない）なら根拠を外す
        if not any(_fee_related(submission_fee, cf) for cf in cited_fees):
            it.knowledge_source = "AI知見"
            it.severity = "要確認"
            it.evidence = "AI判断（本申請の費目に整合する過去事例なし・根拠を不採用）"
    return items


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
