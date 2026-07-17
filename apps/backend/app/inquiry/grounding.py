"""④ 接地検査（DESIGN §3-4）。

Check Grounding API で、生成された回答が根拠レコード本文に支持される度合いを
検査する第二ゲート（D-3）。score < INQUIRY_GROUNDING_THRESHOLD なら
pipeline 側で棄却（low_grounding）に切替える。

実測で判明している挙動（D-13・D-20）：
- support_score は「検査対象（grounding_check_required=true）と分類された主張が
  facts に支持される度合い」。検査対象の主張が1つも無い場合 score=0 になる
  （＝閾値ゲートで棄却される）。メタ言及・指示形の例示は検査対象外に
  分類されやすいため、③は条件平叙文を1文目に置く。
  この構成なら正答は 0.7〜0.95 で通過する（ミニ評価実測）。
- 主張分解は文区切り依存：「。」の直後に空白・改行が無いと複数文が1つの複合主張に
  束ねられ、どの fact にも支持されず score≒0 になる（D-20 実測 0.007）。
  → API に渡す前に文境界へ改行を補う（_normalize_for_claims）。
- fact を1メッセージ単位にすると、会話をまたぐ関係（確認「超過理由を求める」↔
  回答「除染工程増加」）が検証できず、正しい合成回答が「支持なし」になる
  （D-20 実測 0.50）。→ 同一案件の往復を1つの fact に結合する（_build_case_facts）。
"""
import logging
import re

from google.cloud import discoveryengine_v1 as discoveryengine

from apps.backend.app.core.settings import GCP_LOCATION, GCP_PROJECT_ID
from apps.backend.app.inquiry.generation import EVIDENCE_TAG_PATTERN
from apps.backend.app.inquiry.models import ClaimCitation, GroundingResult
from apps.backend.app.inquiry.sufficiency import DIRECTION_LABELS

logger = logging.getLogger(__name__)

# 1 fact あたりの本文上限（防御的な足切り。F3メッセージは通常数百文字）
_FACT_TEXT_MAX_CHARS = 4000

# 「。」の直後に空白・改行が続かない箇所（＝文分割が失敗する箇所・D-20）
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"。(?=[^\s])")

# 案件メタ行として fact に足す案件属性（D-22）。質問はレコード本文でなく
# 案件属性（費目・工事名等）の語彙で書かれることが多く、回答1文目が質問の
# 話題語をなぞると本文だけの fact では支持なしと判定される
# （A-8 実測：「維持管理費の定期点検について、〜」が 0.22→メタ行付与で 0.98）。
# 値が空・「－」（事務連絡等）の属性は行に含めない。
_CASE_META_FIELDS = (
    ("費目", "cost_category"),
    ("工事名", "construction_name"),
    ("提出タイミング", "submission_timing"),
)

# 文頭の接続詞（実測で主張の含意判定を壊す・D-21）。
# 「また、〜が求められます」は fact にほぼ同文があっても支持なしと判定され
# score が 0.63→0.86 に変わることをプローブで確認済み。除去しても主張の
# 内容は変わらないため、検査入力からのみ機械的に取り除く（表示は不変）。
_LEADING_CONNECTIVE_PATTERN = re.compile(
    r"^[ \t]*(?:また|さらに|なお|加えて|そのほか|その他)、", re.MULTILINE
)

_grounding_client: discoveryengine.GroundedGenerationServiceClient | None = None


def _get_client() -> discoveryengine.GroundedGenerationServiceClient:
    global _grounding_client
    if _grounding_client is None:
        _grounding_client = discoveryengine.GroundedGenerationServiceClient()
    return _grounding_client


def _normalize_for_claims(answer: str) -> str:
    """API の主張分解が機能する形に整える（evidence タグ除去＋文境界の改行補完・D-20）。

    タグは本文の主張ではないため除去する。文境界の改行が無いと複数文が
    1つの複合主張に束ねられ score≒0 になるため「。」の直後に改行を補う。
    表示用の回答本文（AskResult.answer）には手を入れない。
    """
    plain = EVIDENCE_TAG_PATTERN.sub("", answer).strip()
    plain = _SENTENCE_BOUNDARY_PATTERN.sub("。\n", plain)
    return _LEADING_CONNECTIVE_PATTERN.sub("", plain)


def _case_meta_line(message: dict) -> str:
    """案件メタ行（費目・工事名・提出タイミング・対象）を組み立てる（D-22）。

    案件レベルの属性は同一案件の全メッセージで共通のため、代表1件から取る。
    属性が何も無い（すべて空・「－」）場合は空文字＝メタ行なし。
    """
    parts = []
    for label, key in _CASE_META_FIELDS:
        value = str(message.get(key, "") or "").strip()
        if value and value != "－":
            parts.append(f"{label}={value}")
    target = "".join(
        str(message.get(key, "") or "").strip().replace("－", "")
        for key in ("plant_site", "plant_unit")
    )
    if target:
        parts.append(f"対象={target}")
    return "案件: " + "・".join(parts) if parts else ""


def _build_case_facts(records: list[dict]) -> list[discoveryengine.GroundingFact]:
    """同一案件の往復メッセージを1つの fact に結合する（1 fact = 1 案件・D-20）。

    fact を1メッセージ単位にすると会話をまたぐ関係が検証できないため、
    ②③に渡している文脈単位（案件＝D-19）と揃える。案件内はメッセージID
    （_doc_id="{id}_{seq}"）順＝時系列に並べ、種別ラベルで発話者を明示する。
    先頭に案件メタ行（D-22）を置き、質問の話題語（費目・工事名）をなぞった
    回答文が本文に無い語彙のせいで支持なしにならないようにする。
    """
    by_case: dict[str, list[dict]] = {}
    for r in records:
        by_case.setdefault(str(r.get("id", "")), []).append(r)

    facts = []
    for case_id, messages in by_case.items():
        lines = []
        meta = _case_meta_line(messages[0])
        if meta:
            lines.append(meta)
        for m in sorted(messages, key=lambda x: str(x.get("_doc_id", ""))):
            direction = str(m.get("message_direction", ""))
            label = DIRECTION_LABELS.get(direction, direction)
            lines.append(f"【{label}】{str(m.get('message_content', '')).strip()}")
        facts.append(
            discoveryengine.GroundingFact(
                fact_text="\n".join(lines)[:_FACT_TEXT_MAX_CHARS],
                attributes={"record_id": case_id},
            )
        )
    return facts


def check_grounding(answer: str, records: list[dict]) -> GroundingResult:
    """answer が records（の message_content）に支持される度合いを検査する。

    answer は _normalize_for_claims で、facts は _build_case_facts で
    それぞれ API の挙動（D-20）に合わせて整形する。
    失敗は例外のまま送出し、pipeline 側で棄却（gate_error）に倒す（DESIGN §6）。
    """
    plain_answer = _normalize_for_claims(answer)
    facts = _build_case_facts(records)

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
