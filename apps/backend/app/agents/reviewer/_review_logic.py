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
from apps.backend.app.core.settings import RERANK_GUARD_F2_THRESHOLD

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
- Tool1（F2）の各レコードは、**NuRO内で共有される知見・確認の勘所**（過去の問合せ対応・委員会論点・
  チェックリスト・工法選定や安全確認の基準など）である。同種の費目・工事に当てはまる知見があれば、
  **F3と同格**で `[F2#N]` を根拠に指摘する（F3に該当事例が無くてもF2にあれば必ず拾う）。
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
- **工事件名が異なっていても費目が同種なら**、過去事例で NuRO が求めた一般的な確認要求
  （積算根拠・内訳明細・物量計算書・工程/年度配分・費用低減策の具体化 等）は本申請にも
  当てはまる限り**根拠として引用してよい**（審査の要求事項は工事をまたいで共通のため）。
  不採用にするのは費目・対象が明らかに別分野の場合のみ。
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

evidence の記載形式（重要）：
  F2/F3 を根拠にする指摘は従来どおり積極的に出すこと。その際、引用は各レコード付属の
  `_ref` の参照番号を**そのまま**記載する（引用をためらう理由にはしないこと）。
  正: "[F3own#3], [F3own#4]"
  誤: "【F3ナレッジ（自社）｜シートKNI_1G_01｜メッセージID 03_..._02】"（IDやシート名へ展開しない）

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


def _content_tokens(text: str) -> set[str]:
    """自由文から連続2文字トークン集合を作る（語の重なり判定用）。

    区切り文字で分割してから各断片を `_fee_tokens` にかけるため、語境界を
    またいだ偽トークンが生じない。費目を持たないナレッジ（F2）の関連性判定に使う。

    Args:
        text: ナレッジの内容テキスト（業務カテゴリ・事象概要・メッセージ内容などを連結したもの）。

    Returns:
        連続2文字トークンの集合。
    """
    tokens: set[str] = set()
    for part in re.split(r"[\s、。,.／/・（）()\[\]「」]+", str(text)):
        tokens |= _fee_tokens(part)
    return tokens


def _record_relevant(submission_fee: str, rec: dict) -> bool:
    """過去ナレッジ1件が本申請のレビュー文脈に関連するか（種別非依存の関連性判定）。

    費目(fee_type)を持つレコード（F3）は申請費目との費目語の重なりで判定する。費目列を
    持たない NuRO 内共有ナレッジ（F2）は、Reranking の意味スコア（`_rerank_score`）が
    あればそれを優先し、無ければ内容語（業務カテゴリ・事象概要・メッセージ内容）と
    申請費目の語の重なりで判定する。いずれも費目名をハードコードしない一般則。

    関連の基準（重要）：F2 のスコアは検索クエリ（`build_search_query`＝**費目＋工事件名**、
    §1-7 で「同じ工事の話題違い事例」も surfacing させるため意図的に広げている）に対する
    関連度である。したがって F2 では「申請費目そのもの」だけでなく **同一工事のNuRO内知見も
    関連とみなす**（これは意図した挙動＝同じ工事の申請レビューに、その工事のNuRO知見を根拠に
    してよい）。全く別工事・別費目の無関係レコードはスコアが低く閾値で除外される（難4を担保）。

    Args:
        submission_fee: 本申請の費目（対象費目1/2 のいずれか）。
        rec: 検索で得たナレッジ1件（費目フィールドの有無で判定方法を切り替える）。

    Returns:
        本申請のレビュー文脈に関連するとみなせれば True。関連が辿れなければ False（＝根拠不採用の対象）。
    """
    # 費目フィールド（fee_type / cost_category）を持つレコード＝F3系。
    # 値が空でも「費目を持つ様式なのに未記入」であり、内容語へフォールバックせず
    # 従来どおり不採用にする（空費目レコードの根拠採用は誤groundingリスク）。
    if "fee_type" in rec or "cost_category" in rec:
        fee = str(rec.get("fee_type") or rec.get("cost_category") or "").strip()
        return _fee_related(submission_fee, fee)
    # 以降は費目フィールドを持たないナレッジ（F2＝NuRO内共有・費目横断）。
    # Reranking の意味スコア（検索クエリ＝費目＋工事件名 への関連度）で字面でなく意味で判定
    # （§1-18 の偽陽性を排除）。例：申請「放射線管理費」に対し内容に「放射性廃棄物」を含むF2は、
    # 2文字"放射"では一致してしまうがクロスエンコーダのスコアは低く、閾値で正しく不採用にできる。
    if "_rerank_score" in rec:
        return float(rec.get("_rerank_score") or 0.0) >= RERANK_GUARD_F2_THRESHOLD
    # スコアが無い場合（Reranking 無効・API失敗）は内容語トークンへフォールバック
    content = " ".join(
        str(rec.get(k, "")) for k in ("business_category", "phenomenon_summary", "message_content")
    )
    if not submission_fee or not content.strip():
        return False
    return bool(_fee_tokens(submission_fee) & _content_tokens(content))


def apply_relevance_guard(
    items: list[ReviewItem],
    mappings: list[dict],
    f2_knowledge: list[dict],
    f3_own: list[dict],
    f3_all: list[dict],
) -> list[ReviewItem]:
    """誤grounding防止（本番堅牢化・難易度4対策）＋引用形式ゆれの決定論解決。

    検索は費目が違っても"近い"docを返すため、LLMが無関係な過去事例を根拠化することがある。
    引用した [F2#N]/[F3own#N]/[F3all#N] が本申請に整合しない場合、その grounding は不当と
    みなし AI知見へ降格する（指摘自体は残し、誤った根拠ラベルだけ外す）。

    引用形式のゆれ対策（Gemini 3.5系対応）：モデルが参照番号でなく散文
    （例「…メッセージID 03_KT_1G_01_0003_02」）で引用した場合も、evidence 内の
    レコードID（_doc_id / message_id）を逆引きして参照を復元し、表示は正準の
    参照番号形式へ正規化する。復元できない引用は従来どおり降格（難4の安全性は不変）。
    プロンプトの意味判断を補う確定的な安全網。特定費目・IDをハードコードしない。

    Args:
        items: Gemini が生成したレビュー指摘のリスト。
        mappings: 転記結果（本申請の費目の取得に使う）。
        f2_knowledge: Tool1 検索結果（F2ナレッジ）。
        f3_own: Tool2a 検索結果（F3自社）。
        f3_all: Tool2b 検索結果（F3他社）。

    Returns:
        根拠の妥当性を検証・正規化した items（同一リストを in-place 更新して返す）。
    """
    submission_fee = next(
        (str(m.get("value", "")).strip() for m in mappings
         if m.get("field_name") in ("対象費目1", "対象費目2") and str(m.get("value", "")).strip()),
        "",
    )

    # 各ナレッジ参照が本申請に関連するかを事前計算する（種別ごとに基準が異なる）。
    #   F3（費目あり）        : 申請費目 ⇔ 過去事例費目 の語の重なり
    #   F2（費目なし=NuRO内知見）: 申請費目 ⇔ 内容語（業務カテゴリ・事象概要・内容）の重なり
    # ※申請費目が取れないシート（MRC2等）は妥当性判定（降格）を行わないが、
    #   引用の解決・正規化（下）は表示一貫性のため常に行う。
    ref_relevant: dict[str, bool] = {}
    # 散文引用の逆引き表：レコードID（_doc_id / message_id）→ 参照キー（F3own#N 等）。
    # モデルが [F3own#N] 形式を守らず「…メッセージID 03_KT_…」等へ展開しても、
    # ID の一致で決定論的に引用を復元できる（Gemini 3.5系で観測された文体ゆれ対策）。
    id_to_ref: dict[str, str] = {}
    for prefix, recs in (("F2", f2_knowledge), ("F3own", f3_own), ("F3all", f3_all)):
        for i, r in enumerate(recs, 1):
            if submission_fee:
                ref_relevant[f"{prefix}#{i}"] = _record_relevant(submission_fee, r)
            for key in ("_doc_id", "message_id"):
                rid = str(r.get(key, "") or "").strip()
                if rid:
                    id_to_ref.setdefault(rid, f"{prefix}#{i}")

    # F2 が1件も関連判定されなかった場合の観測ログ（silently な回帰の早期検知）。
    # F2 grounding は Reranking スコア閾値で判定するため、モデル更新やコーパス変化で
    # スコア分布がずれると全 F2 が閾値未満に落ち、F2根拠が無音で消える（#17 と同型の回帰）。
    f2_total = len(f2_knowledge)
    f2_relevant = sum(1 for k, v in ref_relevant.items() if k.startswith("F2#") and v)
    if submission_fee and f2_total and not f2_relevant:
        logger.warning(
            "関連性ガード: F2ナレッジ %d 件すべてが関連なし判定（費目=%r）。"
            "Reranking のモデル/閾値ずれで F2 grounding が無音で消えていないか要確認",
            f2_total, submission_fee,
        )

    for it in items:
        src = it.knowledge_source or ""
        if "F3" not in src and "F2" not in src:
            continue
        evidence = it.evidence or ""

        # ── 引用の解決（bracket形式＋散文の両対応・出現順を保持）──────────
        # bracket形式（[F3own#N]）の出現位置つき抽出
        found: list[tuple[int, str]] = []  # (出現位置, 参照キー)
        for m in re.finditer(r"(F2|F3own|F3all)#(\d+)", evidence):
            ref = f"{m.group(1)}#{m.group(2)}"
            if ref not in (r for _, r in found):
                found.append((m.start(), ref))
        # 散文引用：evidence に含まれるレコードIDを逆引きする。長いIDから照合し、
        # 消費済み箇所は同長マスクで潰す＝部分文字列の誤解決（親ID⊂メッセージID）を
        # 防ぎつつ、後続IDの出現位置を保存する
        work = evidence
        for rid in sorted(id_to_ref, key=len, reverse=True):
            pos = work.find(rid)
            if pos < 0:
                continue
            ref = id_to_ref[rid]
            if ref not in (r for _, r in found):
                found.append((pos, ref))
            work = work.replace(rid, "\x00" * len(rid))

        refs = [r for _, r in sorted(found)]  # 引用の出現順を維持
        # 表示は正準の参照番号形式へ正規化（2.5系と同じ見た目・UIの一貫性）
        if refs:
            canonical = ", ".join(f"[{r}]" for r in refs)
            if it.evidence != canonical:
                logger.info("関連性ガード: 引用を参照番号へ正規化（%s ← %.60s）", canonical, evidence)
                it.evidence = canonical

        # ── 妥当性判定（降格）：申請費目が取れる場合のみ ─────────────────
        if not submission_fee:
            continue
        # 引用が本申請に整合しない（または引用が辿れない）なら根拠を外す
        if not any(ref_relevant.get(r, False) for r in refs):
            logger.info(
                "関連性ガード: 指摘の根拠を不採用に降格（src=%s evidence_refs=%s）",
                src, refs,
            )
            it.knowledge_source = "AI知見"
            it.severity = "要確認"
            it.evidence = "AI判断（本申請に整合する過去事例なし・根拠を不採用）"
    return items


def _field_anchor_map(frame_name: str, sheet_name: str, kubun: str) -> dict[str, tuple[str, set[str]]]:
    """様式定義（config）から field_name → (推奨セル, 全候補セル集合) の解決マップを作る。

    label_value 項目は単一セル。plan_actual 項目は計画セル(G列)と実績セル(K列)を持つため、
    計画実績区分に応じて推奨セルを選ぶ（計画→plan列／実績→actual列）。費目・セルを
    ハードコードせず、様式定義に書かれた対応のみを使う。表(tabular)は対象外（値のある
    セルが mappings に載り番地は元々正しいため）。

    Args:
        frame_name: 様式名（例 "frameB"）。
        sheet_name: シート名（例 "MRC1"）。
        kubun: 計画実績区分の値（"計画"/"実績"）。実績なら actual セルを推奨する。

    Returns:
        {field_name: (推奨セル番地, {定義セル番地...})}。config が読めなければ空 dict。
    """
    try:
        config = load_frame_config(frame_name, sheet_name)
    except Exception:  # noqa: BLE001 - config欠如時は補正しない（安全側）
        return {}
    prefer_actual = str(kubun).strip() == "実績"
    result: dict[str, tuple[str, set[str]]] = {}

    def _register(field: str, preferred: str, cells: set[str]) -> None:
        # 同一 field が複数セクションに定義される場合（例：炉型＝C7 と G9/K9）は候補を
        # 累積（和集合）し、推奨セルは最初に見つかった定義を維持する。こうしないと
        # 後勝ち上書きで有効セルが消え、正しく付いた番地まで誤補正してしまう。
        if field not in result:
            result[field] = (preferred, set(cells))
        else:
            prev_pref, prev_cells = result[field]
            result[field] = (prev_pref, prev_cells | cells)

    for section in config.get("sections", []):
        stype = section.get("type")
        for field_name, cell_info in section.get("fields", {}).items():
            if stype == "label_value":
                cell = str(cell_info)
                _register(field_name, cell, {cell})
            elif stype == "plan_actual" and isinstance(cell_info, dict):
                plan = str(cell_info.get("plan", "") or "")
                actual = str(cell_info.get("actual", "") or "")
                candidates = {c for c in (plan, actual) if c}
                if not candidates:
                    continue
                preferred = (actual or plan) if prefer_actual else (plan or actual)
                _register(field_name, preferred, candidates)
    return result


def reanchor_review_items(
    items: list[ReviewItem],
    frame_name: str,
    sheet_name: str,
    mappings: list[dict],
) -> list[ReviewItem]:
    """LLMが付けたセル番地を様式定義で検証し、ズレていれば正しい番地へ補正する。

    空欄フィールド（値が無く mappings に載らない項目）への指摘は、LLM がその番地を
    知らず可視セルへ誤爆することがある（例：実施費用低減策の指摘が工事件名 C6 に付く）。
    field_name が様式定義に一致し、かつ LLM の cell_address がその定義セルに含まれない
    場合のみ、計画実績区分に応じた正しいセルへ補正する。**定義に無い field_name
    （表フィールド・LLMの言い換え）は一切触らない**（元番地が正しいことが多く、
    誤った上書きで別バグを作らないための安全側）。特定費目・セルはハードコードしない。

    Args:
        items: パース済みのレビュー指摘リスト。
        frame_name: 様式名。
        sheet_name: シート名。
        mappings: 転記結果 mappings（計画実績区分の取得に使う）。

    Returns:
        cell_address を必要に応じ補正した items（同一リストを in-place 更新して返す）。
    """
    kubun = next(
        (str(m.get("value", "")).strip() for m in mappings
         if m.get("field_name") == "計画実績区分" and str(m.get("value", "")).strip()),
        "",
    )
    anchor_map = _field_anchor_map(frame_name, sheet_name, kubun)
    if not anchor_map:
        return items
    for it in items:
        entry = anchor_map.get(it.field_name)
        if not entry:
            continue  # 様式定義に無い field は温存（表・言い換え）
        preferred, candidates = entry
        if it.cell_address not in candidates:
            logger.info(
                "番地補正: 指摘『%s』の番地を %r→%r に是正（様式定義に基づく）",
                it.field_name, it.cell_address, preferred,
            )
            it.cell_address = preferred
    return items


# LLMが引用する参照番号（[F3own#18] 等）。角括弧は任意（LLMの表記ゆれ許容）。
# 種別は apply_relevance_guard と同一（F2/F3own/F3all）に揃える。
_REF_TOKEN_RE = re.compile(r"\[?(F2|F3own|F3all)#(\d+)\]?")

# カンマ等で連結された参照番号の連なり（例 "[F3own#1], [F3own#2]、[F3all#4]"）。
# 1個以上のトークンを区切り（,、，）で結んだ範囲をまとめて捕捉し、範囲内で重複を畳む。
_REF_RUN_RE = re.compile(
    r"\[?(?:F2|F3own|F3all)#\d+\]?"
    r"(?:\s*[,、，]\s*\[?(?:F2|F3own|F3all)#\d+\]?)*"
)


def _ref_source_label(prefix: str, record: dict) -> str:
    """参照番号1件を、ユーザーに伝わる出典表記へ変換する。

    参照番号は「その回の検索結果の並び順」でしかなく画面上では意味を持たないため、
    レコードが実際に持つ由来情報（由来シート・公式ver5.3メッセージID・電力会社名）で
    出典を示す。メッセージIDはナレッジExcelに実在する行の特定子。

    メッセージIDは `_doc_id`（Vertex 文書ID＝ingest 時の id_field="message_id"＝
    通し連番 {id}_{seq:02d}）を正とする。struct_data の `message_id` 列は索引に載らず
    レコードに現れないため、`id`（スレッド基底ID・同スレッドの複数メッセージで共通）に
    落とすと別メッセージが同一表記に潰れる。順序は _doc_id → message_id → id。

    Args:
        prefix: 参照種別（"F2" / "F3own" / "F3all"）。
        record: 検索ヒットしたナレッジレコード（ver5.3平坦化行）。

    Returns:
        出典表記（例 "【F3ナレッジ（自社：関東電力）｜シートKNI_1G_01｜メッセージID 03_KT_1G_01_0003_03】"）。
        由来情報が全く取れない場合は空文字（呼び出し側で原文温存）。
    """
    sheet = str(record.get("sheet_name", "") or "").strip()
    msg_id = str(
        record.get("_doc_id", "")
        or record.get("message_id", "")
        or record.get("id", "")
        or ""
    ).strip()
    if prefix == "F2":
        source = "F2ナレッジ（NuRO内知見）"
    else:
        scope = "自社" if prefix == "F3own" else "他社"
        utility = str(record.get("utility_name", "") or "").strip()
        source = f"F3ナレッジ（{scope}：{utility}）" if utility else f"F3ナレッジ（{scope}）"
    details = [d for d in (f"シート{sheet}" if sheet else "", f"メッセージID {msg_id}" if msg_id else "") if d]
    if not details:
        return ""
    return "【" + "｜".join([source, *details]) + "】"


def humanize_evidence_refs(
    items: list[ReviewItem],
    f2_knowledge: list[dict],
    f3_own: list[dict],
    f3_all: list[dict],
) -> list[ReviewItem]:
    """指摘文中の参照番号（[F3own#N] 等）をナレッジの実出典表記へ置換する。

    決定論の文字列置換のみ（LLM再呼び出しなし・ハードコードなし）。番号→レコードの
    対応は _number_records と同じ「リストの並び順（1始まり）」を使う。解決できない
    参照（範囲外・由来情報なし）は原文のまま温存する（安全側）。カンマ等で連なった
    参照は連なり単位で処理し、同一出典表記になるものは順序を保って1件に畳む。

    **必ず apply_relevance_guard の後に呼ぶこと**。ガードは参照番号の字面
    （F3own#N 等）で誤groundingを判定するため、先に置換すると判定が壊れる。

    Args:
        items: パース済みのレビュー指摘リスト。
        f2_knowledge: プロンプトに渡した F2 レコード（[F2#N] の並び順そのまま）。
        f3_own: プロンプトに渡した F3 自社レコード（[F3own#N] の並び順そのまま）。
        f3_all: プロンプトに渡した F3 他社レコード（[F3all#N] の並び順そのまま）。

    Returns:
        comment / evidence の参照番号を出典表記に置換した items
        （同一リストを in-place 更新して返す）。
    """
    records_by_prefix = {"F2": f2_knowledge, "F3own": f3_own, "F3all": f3_all}

    def _resolve(prefix: str, num: int) -> str:
        """参照番号1件を出典表記へ。辿れない/由来なしは元トークンを返す。"""
        records = records_by_prefix[prefix]
        if not 1 <= num <= len(records):
            return f"[{prefix}#{num}]"
        return _ref_source_label(prefix, records[num - 1]) or f"[{prefix}#{num}]"

    def _replace_run(match: re.Match) -> str:
        # 連なり内の各参照を出典表記に変換し、同一表記を順序保持で1件に畳む。
        # （同じ事例を複数番号で引くと同一メッセージIDが並ぶため）
        rendered: list[str] = []
        for tok in _REF_TOKEN_RE.finditer(match.group(0)):
            label = _resolve(tok.group(1), int(tok.group(2)))
            if label not in rendered:
                rendered.append(label)
        return ", ".join(rendered)

    for it in items:
        if it.evidence:
            it.evidence = _REF_RUN_RE.sub(_replace_run, it.evidence)
        if it.comment:
            it.comment = _REF_RUN_RE.sub(_replace_run, it.comment)
    return items


_TABLE_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _is_filled(value) -> bool:
    """転記値が「記載あり」とみなせるかを返す（None・空白のみは空欄扱い。0 は記載あり）。"""
    return value is not None and str(value).strip() != ""


def _generate_missing_entry_items(
    mappings: list[dict],
    frame_name: str,
    sheet_name: str,
    required_fields: list[str],
    required_table_columns: dict,
) -> list[ReviewItem]:
    """記載必須欄の空欄を検出し「記入してください」指摘を生成する（決定論ルール）。

    LLMに空欄項目を渡す方式は過検出のため不採用（RAG_VERIFICATION §1-20）。代わりに
    criteria YAML の opt-in 宣言（load_required_entries）と様式定義（config）だけを
    拠り所に、確定的に検出する。特定費目・セルはハードコードしない。

    基本情報系（label_value / plan_actual）：
      セル解決は _field_anchor_map を再利用（計画実績区分で G/K を選択）。
      「mappings に空値で載っている」「mappings に載っていない（未転記）」の両方を空欄とする。
      様式定義に無い必須宣言（設定ミス・別様式の宣言）は黙ってスキップ（安全側）。

    表（tabular）：
      行数は工事ごとに変わるため、アクティブ行＝「mappings 上で表の列に非空値がある行」を
      番地の算術（列文字＋行番号）で導出し、必須列×アクティブ行の空セルを検出する。
      指摘は列単位で1件に集約（1空セル1件だと表で過剰になるため）。

    Args:
        mappings: 転記結果 mappings（{field_name, cell_address, value, ...}）。
        frame_name: 様式名（例 "frameB"）。
        sheet_name: シート名（例 "MRC1"）。
        required_fields: 記載必須のフィールド名リスト（criteria YAML 由来）。
        required_table_columns: {表セクション名: {"共通"/"計画"/"実績": [列名...]}}。

    Returns:
        空欄指摘の ReviewItem リスト。宣言が空・全欄記載済みなら空リスト。
    """
    items: list[ReviewItem] = []
    if not required_fields and not required_table_columns:
        return items

    kubun = next(
        (str(m.get("value", "")).strip() for m in mappings
         if m.get("field_name") == "計画実績区分" and str(m.get("value", "")).strip()),
        "",
    )

    filled_fields = {m.get("field_name") for m in mappings if _is_filled(m.get("value"))}
    filled_cells = {m.get("cell_address") for m in mappings if _is_filled(m.get("value"))}

    # ── 基本情報系：様式定義のセル解決で空欄を検出 ──────────────────────────
    if required_fields:
        anchor_map = _field_anchor_map(frame_name, sheet_name, kubun)
        for field in required_fields:
            entry = anchor_map.get(field)
            if not entry:
                continue  # 様式定義に無い宣言は補正しない（設定ミス保護）
            preferred, candidates = entry
            if field in filled_fields or (candidates & filled_cells):
                continue
            items.append(ReviewItem(
                item_id="",
                field_name=field,
                cell_address=preferred,
                severity="要確認",
                comment=f"「{field}」が空欄です。記入してください。",
                evidence="AI判断（ナレッジ参照なし）：様式定義上の記載必須欄が空欄",
                knowledge_source="AI知見",
            ))

    # ── 表：アクティブ行（転記済み行）×必須列で空欄を検出 ──────────────────
    if required_table_columns:
        try:
            config = load_frame_config(frame_name, sheet_name)
        except Exception:  # noqa: BLE001 - config欠如時はチェックしない（安全側）
            return items
        for section in config.get("sections", []):
            if section.get("type") != "tabular":
                continue
            sec_name = str(section.get("name", ""))
            req = required_table_columns.get(sec_name)
            if not req:
                continue
            col_by_name = {
                str(c.get("name", "")): str(c.get("column", "")).upper()
                for c in section.get("columns", []) if c.get("column")
            }
            group = "実績" if kubun == "実績" else "計画"
            wanted = list(req.get("共通") or []) + list(req.get(group) or [])
            pairs = [(name, col_by_name[name]) for name in wanted if col_by_name.get(name)]
            if not pairs:
                continue

            data_start = int(section.get("data_start_row") or 0)
            total_row = section.get("total_row")
            table_letters = set(col_by_name.values())

            # アクティブ行と、表セル（列文字, 行番号）→記載有無 を mappings から導出
            active_rows: set[int] = set()
            cell_filled: dict[tuple[str, int], bool] = {}
            for m in mappings:
                match = _TABLE_CELL_RE.match(str(m.get("cell_address", "") or ""))
                if not match:
                    continue
                letter, row = match.group(1), int(match.group(2))
                if letter not in table_letters or row < data_start:
                    continue
                if total_row is not None and row == int(total_row):
                    continue
                filled = _is_filled(m.get("value"))
                cell_filled[(letter, row)] = cell_filled.get((letter, row), False) or filled
                if filled:
                    active_rows.add(row)

            for col_name, letter in pairs:
                empty_addrs = [
                    f"{letter}{row}" for row in sorted(active_rows)
                    if not cell_filled.get((letter, row), False)
                ]
                if not empty_addrs:
                    continue
                items.append(ReviewItem(
                    item_id="",
                    field_name=f"{sec_name}_{col_name}",
                    cell_address=empty_addrs[0],
                    severity="要確認",
                    comment=(
                        f"{sec_name}の「{col_name}」列に空欄があります"
                        f"（{', '.join(empty_addrs)}）。記入するか、対象外の理由を記載してください。"
                    ),
                    evidence="AI判断（ナレッジ参照なし）：転記済みの表行に記載必須列の空欄",
                    knowledge_source="AI知見",
                ))
    return items


def merge_rule_and_gemini_items(
    rule_items: list[ReviewItem],
    gemini_items: list[ReviewItem],
) -> list[ReviewItem]:
    """ルールベース指摘と Gemini 指摘を「1セル1指摘」でマージする。

    原則はルール指摘（決定論）が優先だが、**F2/F3 根拠つきの Gemini 指摘がある
    セルは Gemini 側を採用**する。ルール指摘（例「記載必須欄が空欄」）は LLM が
    見落とした場合の決定論的な保険であり、同じセルに過去事例を根拠にした指摘が
    既にあるなら、そちらの方が情報量で勝るため（本関数は apply_relevance_guard の
    後に呼ばれる前提＝F2/F3 ラベルは関連性検証済み）。

    Args:
        rule_items: rule_check_node 由来の決定論指摘（プレースホルダー・必須欄空欄等）。
        gemini_items: ガード・番地補正・出典可読化を通過した Gemini 指摘。

    Returns:
        マージ済みの指摘リスト（ルール指摘 → Gemini 指摘の順。item_id は未採番）。
    """
    grounded_cells = {
        i.cell_address for i in gemini_items
        if "F2" in (i.knowledge_source or "") or "F3" in (i.knowledge_source or "")
    }
    rule_cells = {r.cell_address for r in rule_items}
    kept_rules = [r for r in rule_items if r.cell_address not in grounded_cells]
    kept_gemini = [
        i for i in gemini_items
        if i.cell_address not in rule_cells or i.cell_address in grounded_cells
    ]
    return kept_rules + kept_gemini


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
