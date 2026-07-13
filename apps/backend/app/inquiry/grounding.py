"""④ 接地検査（DESIGN §3-4）。

Check Grounding API で、生成された回答が根拠レコード本文に支持される度合いを
検査する第二ゲート（D-3）。score < INQUIRY_GROUNDING_THRESHOLD なら
pipeline 側で棄却（low_grounding）に切替える。

実測で判明している挙動（D-13）：
- support_score は「検査対象（grounding_check_required=true）と分類された主張が
  facts に支持される度合い」。検査対象の主張が1つも無い場合 score=0 になる
  （＝閾値ゲートで棄却される）。メタ言及・過去事象の再叙述・指示形の例示は
  検査対象外に分類されやすいため、③は条件平叙文を1文目に置く。
  この構成なら正答は 0.7〜0.95 で通過する（ミニ評価実測）。
"""
import logging

from google.cloud import discoveryengine_v1 as discoveryengine

from apps.backend.app.core.settings import GCP_LOCATION, GCP_PROJECT_ID
from apps.backend.app.inquiry.generation import EVIDENCE_TAG_PATTERN
from apps.backend.app.inquiry.models import ClaimCitation, GroundingResult

logger = logging.getLogger(__name__)

# 1 fact あたりの本文上限（防御的な足切り。F3メッセージは通常数百文字）
_FACT_TEXT_MAX_CHARS = 4000

_grounding_client: discoveryengine.GroundedGenerationServiceClient | None = None


def _get_client() -> discoveryengine.GroundedGenerationServiceClient:
    global _grounding_client
    if _grounding_client is None:
        _grounding_client = discoveryengine.GroundedGenerationServiceClient()
    return _grounding_client


def check_grounding(answer: str, records: list[dict]) -> GroundingResult:
    """answer が records（の message_content）に支持される度合いを検査する。

    answer からは evidence タグ（[F3#...]）を除去して API に渡す
    （タグは本文の主張ではないため、主張分解を汚さない）。
    失敗は例外のまま送出し、pipeline 側で棄却（gate_error）に倒す（DESIGN §6）。
    """
    plain_answer = EVIDENCE_TAG_PATTERN.sub("", answer).strip()

    facts = [
        discoveryengine.GroundingFact(
            fact_text=str(r.get("message_content", ""))[:_FACT_TEXT_MAX_CHARS],
            attributes={
                "record_id": str(r.get("id", "")),
                "message_direction": str(r.get("message_direction", "")),
            },
        )
        for r in records
    ]

    client = _get_client()
    # default_grounding_config は locations/global のみに存在する。GCP_LOCATION は
    # 検索側 serving_config とも共有される env（既定 "global"）のためそのまま使う
    grounding_config = (
        f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
        f"/groundingConfigs/default_grounding_config"
    )
    # grounding_spec.citation_threshold（主張への引用付与カットオフ）は指定しない：
    # ④ゲートの INQUIRY_GROUNDING_THRESHOLD とは別のノブであり、同じ値を渡すと
    # 閾値較正（フェーズ4）でゲートを動かすたびに score 自体も動く二重効果が生じる
    response = client.check_grounding(
        discoveryengine.CheckGroundingRequest(
            grounding_config=grounding_config,
            answer_candidate=plain_answer,
            facts=facts,
        )
    )

    result = GroundingResult(
        score=float(response.support_score),
        claim_citations=[
            ClaimCitation(
                claim_text=claim.claim_text,
                citation_indices=list(claim.citation_indices),
            )
            for claim in response.claims
        ],
    )
    logger.info("接地検査: score=%.3f claims=%d", result.score, len(result.claim_citations))
    return result
