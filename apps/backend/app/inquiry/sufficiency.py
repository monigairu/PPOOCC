"""② 十分性判定（DESIGN §3-2）。

検索結果 records で question に回答できるかを、回答生成（③）とは独立の
LLM判定として実行する。棄却をモデルの自制に頼らないための第一ゲート（D-3）。
判定は「部分的にしか答えられない場合は insufficient 側に倒す」（誤答より棄却）。
"""
import logging

from apps.backend.app.core.ai_client import call_gemini_structured
from apps.backend.app.inquiry.config import INQUIRY_MODEL
from apps.backend.app.inquiry.models import SufficiencyResult

logger = logging.getLogger(__name__)

# message_direction（実データ語彙 nuro/denryoku・D-11）のLLM向け表示。
# 未知値はそのまま見せる（判定を止めない）。②③のプロンプトと④の fact 結合（D-20）で共用
DIRECTION_LABELS = {"nuro": "NuRO確認", "denryoku": "電力回答"}
_DIRECTION_LABELS = DIRECTION_LABELS  # 後方互換の別名


def render_records(records: list[dict]) -> str:
    """load_f3 のレコード群をプロンプト用テキストに整形する（②③で共用）。

    1行=1メッセージ（D-9）のまま、案件ID・シート・round・方向を明示して並べる。
    LLM が引用に使うキーは案件ID（record_id ← "id"）である点を崩さない。
    """
    blocks = []
    for i, r in enumerate(records, start=1):
        direction = str(r.get("message_direction", ""))
        direction_label = _DIRECTION_LABELS.get(direction, direction)
        blocks.append(
            f"[レコード{i}] record_id={r.get('id', '')} "
            f"sheet={r.get('sheet_name', '')} "
            f"round={r.get('round', '')} 種別={direction_label}\n"
            f"本文: {str(r.get('message_content', '')).strip()}"
        )
    return "\n\n".join(blocks)


_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "sufficient": {"type": "BOOLEAN"},
        "usable_record_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reason": {"type": "STRING"},
    },
    "required": ["sufficient", "usable_record_ids", "reason"],
}

_SYSTEM_INSTRUCTION = (
    "あなたは電力会社の廃炉業務に関する問い合わせ対応システムの審査担当です。"
    "検索でヒットした社内F3ナレッジ（過去の問合せ履歴）だけを根拠に、"
    "質問へ回答できるかどうかを判定します。回答の生成はしません。"
)

_PROMPT_TEMPLATE = """\
## 質問
{question}

## 検索でヒットしたF3ナレッジ
{records_text}

## 判定基準（厳格に適用すること）
- レコード本文が質問の核心に**直接**答えている場合のみ sufficient=true。
- 以下はすべて sufficient=false（insufficient）とする：
  - 部分的にしか答えられない（答えられる部分があっても不十分側に倒す）
  - 話題は関連するが、質問への答えそのものは書かれていない
  - 回答にはレコード本文にない一般知識・推測・補完が必要
  - 質問が他社事例・他社実績を求めているが、レコードは自社の記録しかない
- usable_record_ids には、sufficient=true の場合に根拠として使える record_id
  （案件ID。例 "03_KT_1G_01_0002"）のみを列挙する。false の場合は空配列。
- reason には判定理由を1〜2文で書く。
"""


def check_sufficiency(question: str, records: list[dict]) -> SufficiencyResult:
    """検索結果 records で question に回答できるかを独立LLM判定する。

    失敗（API エラー・スキーマ不整合等）は例外のまま送出し、pipeline 側で
    棄却（gate_error）に倒す（DESIGN §6）。
    """
    prompt = _PROMPT_TEMPLATE.format(
        question=question,
        records_text=render_records(records),
    )
    raw = call_gemini_structured(
        prompt,
        response_schema=_RESPONSE_SCHEMA,
        model_name=INQUIRY_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    result = SufficiencyResult.model_validate(raw)
    logger.info(
        "十分性判定: sufficient=%s usable=%s reason=%s",
        result.sufficient, result.usable_record_ids, result.reason,
    )
    return result
