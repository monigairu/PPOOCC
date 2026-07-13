"""③ 引用付き回答生成（DESIGN §3-3）。

②で使えると判定されたレコード本文のみを根拠に、evidence タグ
（[F3#record_id]・事前レビューと同記法＝D-5）付きの回答を自前生成する。
Answer API は F3 BQ エンジンで使用不可のため不採用（D-2・2026-07-13 確定）。

文体は「〜の場合、〜が必要です」の**条件平叙文（直接回答型）**を先頭に置く（D-13）：
Check Grounding API は文単位で検査要否を分類し、検査対象の主張が1つも無いと
score=0 で④ゲートを通過できない。実測では条件平叙文が最も確実に検査対象になり
（score 0.98）、メタ言及（「〜と記録されています」）・過去の個別事象の再叙述・
指示形の例示（「例えば…のように記載します」）は検査対象外に分類されやすい。
"""
import logging
import re

from apps.backend.app.core.ai_client import call_gemini
from apps.backend.app.inquiry.config import INQUIRY_MODEL
from apps.backend.app.inquiry.models import GeneratedAnswer
from apps.backend.app.inquiry.sufficiency import render_records

logger = logging.getLogger(__name__)

# evidence タグ（例: [F3#03_KT_1G_01_0002]）。grounding.py のタグ除去でも使う
EVIDENCE_TAG_PATTERN = re.compile(r"\[F3#([0-9A-Za-z_\-]+)\]")

_SYSTEM_INSTRUCTION = (
    "あなたは電力会社の廃炉業務に関する問い合わせ対応システムの回答担当です。"
    "社内F3ナレッジ（過去にNuROと電力会社の間で交わされた問合せ履歴）に"
    "記録されている内容だけを根拠に回答します。"
)

_PROMPT_TEMPLATE = """\
## 質問
{question}

## 根拠にしてよいF3ナレッジ（これ以外の情報は使用禁止）
{records_text}

## 回答の作成ルール（すべて必須）
1. 上記レコード本文に書かれている内容**のみ**で回答する。
   一般知識・推測・レコードにない補完は一切書かない。
2. **1文目は質問に直接答える条件平叙文**にする：
   「〜の場合、〜が必要です」「〜には、〜が求められます」の形。
   以下の表現は使わない：
   - メタ言及（「〜と記録されています」「過去の問合せでは〜」「ナレッジによると〜」）
   - 読み手への指示形の例示（「例えば…のように記載します」）
3. 2文目以降でレコードにある具体例・数値を補足してよい。この場合も平叙文で書く。
4. 各文の末尾に、根拠レコードの evidence タグを必ず付ける。
   形式: [F3#record_id]（例: [F3#03_KT_1G_01_0002]）。record_id は上記レコードの
   record_id をそのまま使う。実在しない record_id を作らない。
5. タグを付けられない文（根拠のない文）は書かない。
6. 簡潔に、質問に直接答える。前置き・免責・挨拶は不要。

## 回答
"""


def generate_answer(question: str, records: list[dict]) -> GeneratedAnswer:
    """records の本文のみを根拠に evidence タグ付き回答を生成する。

    cited_record_ids は answer 本文のタグをパースし、さらに**入力レコードに実在する
    record_id のみに絞って**導出する（タグのでっち上げを引用として採用しない）。
    失敗は例外のまま送出し、pipeline 側で棄却（gate_error）に倒す（DESIGN §6）。
    """
    prompt = _PROMPT_TEMPLATE.format(
        question=question,
        records_text=render_records(records),
    )
    answer = call_gemini(
        prompt,
        model_name=INQUIRY_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )

    known_ids = {str(r.get("id", "")) for r in records}
    tags = EVIDENCE_TAG_PATTERN.findall(answer)
    cited = [t for t in dict.fromkeys(tags) if t in known_ids]  # 出現順を保って重複排除

    unknown = set(tags) - known_ids
    if unknown:
        logger.warning("回答中に実在しない record_id のタグ: %s（引用に採用しない）", sorted(unknown))

    return GeneratedAnswer(answer=answer, cited_record_ids=cited)
