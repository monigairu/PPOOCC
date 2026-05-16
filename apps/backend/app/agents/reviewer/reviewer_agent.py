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

from apps.backend.app.agents.reviewer import knowledge_loader
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


async def run_review(
    session_id: str,
    utility_name: str,
    mappings: list[dict],
    frame_name: str = "frameB",
    sheet_name: str = "MRC1",
    reactor_type: str | None = None,
    fee_type: str | None = None,
) -> list[ReviewItem]:
    """
    4つの観点（Tool）でナレッジ収集し、Gemini にレビューを依頼して指摘リストを返す。

    PoC：caller_role="NuRO" で固定して knowledge_loader を呼び出す
    本番移行時：引数に caller_role: str を追加して knowledge_loader に渡すだけでよい
                このファイル内の他の処理は変更不要

    Args:
        session_id:   FirestoreのセッションID（ログ・追跡用）
        utility_name: 電力会社名
        mappings:     転記結果（field_name, cell_address, value, reasoning のリスト）
        frame_name:   様式名
        sheet_name:   シート名
        reactor_type: 炉型（ナレッジフィルタ用）
        fee_type:     費目（ナレッジフィルタ用）

    Returns:
        ReviewItem のリスト
    """
    # mappings からメタ情報を補完
    if not reactor_type:
        reactor_type = _extract_field(mappings, "炉型")
    if not fee_type:
        fee_type = _extract_field(mappings, "対象費目1")

    # ── Tool 1: 自社の過去指摘事例 ────────────────────────────────────
    own_history = knowledge_loader.load_f3(
        caller_role="NuRO",
        utility_name=utility_name,
        reactor_type=reactor_type,
        fee_type=fee_type,
        limit=30,
    )

    # ── Tool 2: 他社の類似事例（NuROは全社参照可） ───────────────────
    similar_cases = knowledge_loader.load_f3(
        caller_role="NuRO",
        utility_name=None,  # 全社
        reactor_type=reactor_type,
        fee_type=fee_type,
        limit=30,
    )

    # ── Tool 3: 計画・実績差分（実績提出時のみ、計画時は空リスト） ───
    plan_diffs = detect_plan_diff(mappings, frame_name=frame_name, sheet_name=sheet_name)

    # ── Tool 4: F2ナレッジ（NuRO内有）────────────────────────────────
    f2_knowledge = knowledge_loader.load_f2(
        caller_role="NuRO",
        fee_type=fee_type,
        limit=20,
    )

    # ── Tool 5: 補足資料（テキスト部分のみ、写真はPhase3対応） ────────
    # data/knowledge/supplement/ が存在しない場合は空リストが返る（エラーなし）
    # Phase 3：Gemini 2.0 Flash のマルチモーダルで has_images=True の資料も処理
    supplement_info = knowledge_loader.load_supplement(
        caller_role="NuRO",
        utility_name=utility_name,
        fee_type=fee_type,
        limit=20,
    )

    # ── Gemini でレビュー生成 ──────────────────────────────────────────
    prompt = _build_prompt(
        mappings=mappings,
        own_history=own_history,
        similar_cases=similar_cases,
        plan_diffs=plan_diffs,
        f2_knowledge=f2_knowledge,
        supplement_info=supplement_info,
        utility_name=utility_name,
        sheet_name=sheet_name,
    )

    raw_response = call_gemini(prompt)
    return _parse_review_response(raw_response)


def _extract_field(mappings: list[dict], field_name: str) -> str | None:
    """mappings から指定フィールドの値を取り出す"""
    for m in mappings:
        if m.get("field_name") == field_name:
            val = str(m.get("value", "")).strip()
            return val if val else None
    return None


def _build_prompt(
    mappings: list[dict],
    own_history: list[dict],
    similar_cases: list[dict],
    plan_diffs: list[dict],
    f2_knowledge: list[dict],
    supplement_info: list[dict],
    utility_name: str,
    sheet_name: str,
) -> str:
    mappings_text = json.dumps(mappings, ensure_ascii=False, indent=2)
    own_history_text = json.dumps(own_history, ensure_ascii=False, indent=2) if own_history else "（なし）"
    similar_text = json.dumps(similar_cases, ensure_ascii=False, indent=2) if similar_cases else "（なし）"
    plan_diff_text = json.dumps(plan_diffs, ensure_ascii=False, indent=2) if plan_diffs else "（なし、または計画提出のため差分チェック不要）"
    f2_text = json.dumps(f2_knowledge, ensure_ascii=False, indent=2) if f2_knowledge else "（なし）"
    supplement_text = json.dumps(supplement_info, ensure_ascii=False, indent=2) if supplement_info else "（なし）"

    return f"""あなたはNuRO（廃炉管理機構）の審査担当AIです。
電力会社（{utility_name}）が提出した{sheet_name}様式の転記結果をレビューしてください。

## レビュー対象の転記結果
{mappings_text}

## 参照ナレッジ

### Tool1: {utility_name} の過去指摘事例
{own_history_text}

### Tool2: 他社の類似事例
{similar_text}

### Tool3: 計画・実績の差分（実績提出時のみ）
{plan_diff_text}

### Tool4: NuROナレッジ（F2）
{f2_text}

### Tool5: 補足資料（工事概要・テキスト情報）
{supplement_text}

## 指示

上記のナレッジと転記結果を照合し、以下の観点で指摘事項をリストアップしてください。

1. 過去に同様の指摘があった箇所（Tool1・Tool2参照）
2. 計画値と実績値の乖離が大きい箇所（Tool3参照）
3. 必須記載が不十分な箇所（Tool4・ナレッジ参照）
4. 補足資料の内容と転記結果に不整合がある箇所（Tool5参照）

## AI知見で指摘する場合の制約（必ず守ること）

ナレッジが（なし）の場合でも、以下の観点で指摘を行ってください。
ただし、下記のルールを厳守してください。

### 指摘できる観点（ナレッジなしの場合）

- 記載の具体性が不十分：「〜に努める」「適切に実施する」等の曖昧な表現のみで、具体的な施策・手順・数値が記載されていない
- 論理的な不整合：計画時の記載と実績時の記載が矛盾している、または説明が繋がっていない
- 空欄・未記載：通常記載が期待される項目が空欄になっている

### 絶対に行ってはいけないこと（ハルシネーション防止）

- 具体的な法令条文・通達番号・数値基準（「〇〇条」「〇〇%以内」等）を根拠にした指摘
- 確認できない情報を「規制で定められている」「法令上必要」と表現すること
- 参照ナレッジに記載されていない事実を「過去に指摘された」と表現すること
- ナレッジ（なし）なのに knowledge_source を "F2" や "F3" と記載すること

### ナレッジ参照なしで指摘する場合の出力ルール

- severity: 必ず **"要確認"**（断定を避ける）
- evidence: 必ず **"AI判断（ナレッジ参照なし）："** で始め、判断の根拠を簡潔に述べる
- knowledge_source: **"AI知見"** とする

## 出力形式（必ずこのJSONのみを返してください。前後に説明文を入れないでください）

{{
  "review_items": [
    {{
      "field_name": "フィールド名",
      "cell_address": "セル番地（例: K22）",
      "severity": "要確認 または AIからの指摘",
      "comment": "指摘内容（自然言語で具体的に）",
      "evidence": "根拠（ナレッジ引用 または 'AI判断（ナレッジ参照なし）: 〇〇のため'）",
      "knowledge_source": "F2 または F3 または 計画差分 または AI知見"
    }}
  ],
  "summary": "全体的なレビュー所見（2〜3文）"
}}

severity の使い分け：
  "要確認"     → NuROの判断が必要な指摘、またはナレッジ参照なしの指摘
  "AIからの指摘" → ナレッジに明確な根拠がある場合のみ使用可能

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
