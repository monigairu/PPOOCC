"""3段パイプラインの入口（DESIGN §3-1・§1-2）。

質問 → ① 検索（load_f3 再利用・D-1）→ ② 十分性判定 → ③ 引用付き生成 → ④ 接地検査
の順に実行し、回答（answered）または棄却（abstained）を返す。

エラー方針（DESIGN §6）：
- ① 検索失敗 = KnowledgeSearchError を**そのまま送出**（routes 側で 502。
  「ナレッジなし」と誤認させると偽の棄却になるため、棄却で吸収しない）。
- ②③④ の失敗 = 棄却に倒す（abstain_reason="gate_error"・failed_stage に記録）。
  ゲートを通過していない回答は出さない（誤答より棄却）。
"""
import logging
from functools import lru_cache

from apps.backend.app.config.paths import KNOWLEDGE_ROOT
from apps.backend.app.inquiry.config import (
    INQUIRY_EXPAND_CASE_CONTEXT,
    INQUIRY_EXPAND_MAX_CASES,
    INQUIRY_GROUNDING_THRESHOLD,
    INQUIRY_RELATED_LIMIT,
    INQUIRY_TOP_K,
)
from apps.backend.app.inquiry.generation import generate_answer
from apps.backend.app.inquiry.grounding import check_grounding
from apps.backend.app.inquiry.models import AskResult, Evidence
from apps.backend.app.inquiry.sufficiency import check_sufficiency
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    load_f3,
    normalize_utility,
)

logger = logging.getLogger(__name__)

# Evidence.snippet の抜粋上限（「全文貼付にしない」の担保・models.py docstring）
_SNIPPET_MAX_CHARS = 200


def ask(question: str, utility: str, *, top_k: int | None = None) -> AskResult:
    """3段パイプラインを実行して回答 or 棄却を返す。

    utility: 問い合わせ元電力会社名（自社フィルタに使用。正規化は load_f3 側）。
    例外方針は DESIGN §6（検索失敗=KnowledgeSearchError 送出／判定・検査失敗=棄却に倒す）。
    """
    # ① 検索：自社F3のみ（REQUIREMENTS §0-3）。検索障害は例外のまま上げる（D-14）
    records = load_f3(
        caller_role="電力",
        utility_name=utility,
        fee_type=question,
        limit=top_k or INQUIRY_TOP_K,
        raise_on_error=True,
    )

    # D-7: 検索0件なら②をスキップして即棄却（高速・安価。B群の大半がここで落ちる）
    if not records:
        logger.info("検索0件のため即棄却: %r", question)
        return _abstain("insufficient_context", related=[])

    # D-19: ヒットした案件の往復メッセージ（確認・回答の対）を補完し②③の材料を完全にする
    if INQUIRY_EXPAND_CASE_CONTEXT:
        records = _expand_case_context(records, utility)

    def related() -> list[Evidence]:
        """棄却時のみ使う近傍ナレッジ。answered 経路で無駄な構築をしない。"""
        return _to_evidences(records[:INQUIRY_RELATED_LIMIT])

    # ② 十分性判定（第一ゲート・D-3）
    try:
        sufficiency = check_sufficiency(question, records)
    except Exception:
        logger.exception("十分性判定に失敗（棄却に倒す）")
        return _abstain("gate_error", related=related(), failed_stage="sufficiency")
    if not sufficiency.sufficient:
        logger.info("十分性判定で棄却: %s", sufficiency.reason)
        return _abstain("insufficient_context", related=related())

    # ③ 引用付き回答生成（②が使えると判定したレコードに限定）
    usable_ids = set(sufficiency.usable_record_ids)
    usable_records = [r for r in records if str(r.get("id", "")) in usable_ids]
    if not usable_records:
        # sufficient なのに usable が検索結果と一致しない＝判定出力が信頼できない
        logger.warning("usable_record_ids が検索結果と不一致（棄却に倒す）: %s", usable_ids)
        return _abstain("gate_error", related=related(), failed_stage="sufficiency")
    try:
        generated = generate_answer(question, usable_records)
    except Exception:
        logger.exception("回答生成に失敗（棄却に倒す）")
        return _abstain("gate_error", related=related(), failed_stage="generation")
    if not generated.cited_record_ids:
        # 引用の無い回答は契約違反（REQUIREMENTS §0-4）。出さずに棄却する
        logger.warning("生成回答に有効な evidence タグが無い（棄却に倒す）")
        return _abstain("gate_error", related=related(), failed_stage="generation")

    # ④ 接地検査（第二ゲート・D-3）
    try:
        grounding = check_grounding(generated.answer, usable_records)
    except Exception:
        logger.exception("接地検査に失敗（棄却に倒す）")
        return _abstain("gate_error", related=related(), failed_stage="grounding")
    if grounding.score < INQUIRY_GROUNDING_THRESHOLD:
        logger.info(
            "接地スコア %.3f < 閾値 %.3f のため棄却",
            grounding.score, INQUIRY_GROUNDING_THRESHOLD,
        )
        return _abstain("low_grounding", related=related())

    cited_ids = set(generated.cited_record_ids)
    evidences = _to_evidences(
        [r for r in usable_records if str(r.get("id", "")) in cited_ids]
    )
    return AskResult(
        status="answered",
        answer=generated.answer,
        evidences=evidences,
        grounding_score=grounding.score,
    )


def _expand_case_context(records: list[dict], utility: str) -> list[dict]:
    """同一案件の往復メッセージを補完取得する（small-to-big retrieval・D-19）。

    F3は1行=1メッセージ（D-9）のため、①の検索が会話の片側（NuRO確認のみ等）
    しか返さないことがある。ヒット上位の案件IDをクエリに再検索し、同一案件の
    全メッセージ（確認・回答の対）を検索結果の末尾に加える。
    - 末尾追加＝元の順位は不変（related の先頭スライスにも影響しない）。
    - 補完は品質向上のベストエフォート：失敗しても検索本体の結果で続行する
      （①本体の障害=502 とは扱いを分ける。D-14 のリランク失敗と同じ境界の考え方）。
    """
    seen_docs = {r.get("_doc_id") for r in records}
    case_ids: list[str] = []
    for r in records:
        case_id = str(r.get("id", ""))
        if case_id and case_id not in case_ids:
            case_ids.append(case_id)
        if len(case_ids) >= INQUIRY_EXPAND_MAX_CASES:
            break

    expanded = list(records)
    for case_id in case_ids:
        try:
            hits = load_f3(
                caller_role="電力",
                utility_name=utility,
                fee_type=case_id,
                limit=INQUIRY_TOP_K,
            )
        except Exception:
            logger.warning("案件 %s の文脈補完に失敗（検索結果のみで続行）", case_id, exc_info=True)
            continue
        for h in hits:
            # 案件IDクエリはハイブリッド検索のため他案件も混ざる→同一案件のみ採用
            if str(h.get("id", "")) != case_id or h.get("_doc_id") in seen_docs:
                continue
            seen_docs.add(h.get("_doc_id"))
            # IDクエリ由来のスコアは元クエリと比較不能のため落とす（表示の誤解防止）
            expanded.append(dict(h, _rerank_score=None))
    if len(expanded) > len(records):
        logger.info(
            "文脈補完: %d→%d件（対象案件 %s）", len(records), len(expanded), case_ids
        )
    return expanded


def _abstain(
    reason: str,
    *,
    related: list[Evidence],
    failed_stage: str | None = None,
) -> AskResult:
    """棄却応答を組み立てる（棄却は正常系＝起票への正規フォールバック）。"""
    return AskResult(
        status="abstained",
        related=related,
        abstain_reason=reason,
        failed_stage=failed_stage,
    )


def _to_evidences(records: list[dict]) -> list[Evidence]:
    """load_f3 レコード群を Evidence に変換する（対応表は models.py docstring）。

    重複排除は citation_key（record_id + round + message_direction＝D-9）単位。
    """
    evidences: list[Evidence] = []
    seen: set[tuple] = set()
    for r in records:
        evidence = Evidence(
            record_id=str(r.get("id", "")),
            sheet=str(r.get("sheet_name", "")),
            snippet=str(r.get("message_content", "")).strip()[:_SNIPPET_MAX_CHARS],
            source_file=_derive_source_file(r.get("utility_name")),
            score=r.get("_rerank_score"),
            round=_to_int_or_none(r.get("round")),
            message_direction=str(r.get("message_direction")) if r.get("message_direction") else None,
        )
        if evidence.citation_key in seen:
            continue
        seen.add(evidence.citation_key)
        evidences.append(evidence)
    return evidences


@lru_cache(maxsize=32)
def _derive_source_file(utility_name) -> str | None:
    """utility_name から F3 正本ファイル名を導出する（実在する場合のみ・D-11）。

    正本の対応は data/knowledge/schema/*.yaml の excel_file が持つが、BQ平坦化
    レコードに原本ファイル名の列が無いため、ここでは「会社名サフィックス付き
    ファイルが実在する場合のみ」導出する（例: F3_knowledge_関東電力.xlsx）。
    汎用ファイル（F3_knowledge.xlsx）に収録された会社は None（推測で埋めない）。
    lru_cache で同一会社の重複 stat を抑止（1リクエスト内は常に同一会社）。
    """
    normalized = normalize_utility(str(utility_name)) if utility_name else ""
    if not normalized:
        return None
    candidate = f"F3_knowledge_{normalized}.xlsx"
    return candidate if (KNOWLEDGE_ROOT / candidate).exists() else None


def _to_int_or_none(value) -> int | None:
    """BQ由来の round は数値/文字列/浮動小数のゆれがあるため防御的に変換する。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
