"""
チャット編集エージェント

ユーザーの自然言語による編集指示を解釈し、Excelセルを書き換える。

公開関数:
  handle_unified_chat() → 意図判定 + Q&A応答 or 編集指示を1回のLLM呼び出しで処理
  parse_edit_intent()   → 編集意図のみを解析（後方互換、/chat_edit 直接呼び出し用）
  apply_cell_edit()     → YAML ルックアップ + Excel 書き込み（決定論的）

LLM の仕事は「何を変えたいか」の意図解釈のみ。
セル番地の決定は YAML による確定ルックアップで行う。
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from apps.backend.app.core.ai_client import call_gemini
from apps.backend.app.core.cell_writer import write_to_cell
from apps.backend.app.core.excel_io import load_workbook_file, save_workbook_file
from apps.backend.app.core.frame_config_loader import extract_cell_definitions, load_frame_config
from apps.backend.app.core.settings import OUTPUT_DIR
from apps.backend.app.core.skill_loader import load_skill, render_skill


@dataclass
class EditIntent:
    """LLM が解析した編集意図。"""
    status: str                          # "edit" | "ambiguous" | "not_edit"
    field: str | None = None             # 変更対象フィールド名（status=="edit" 時）
    new_value: str | None = None         # 新しい値（status=="edit" 時）
    clarification_question: str | None = None  # 確認質問（status=="ambiguous" 時）
    reason: str | None = None            # 非編集と判断した理由（status=="not_edit" 時）
    confidence: float = 0.0


@dataclass
class EditResult:
    """セル書き込み結果。"""
    field_name: str
    cell_addresses: list[str] = field(default_factory=list)
    new_value: str = ""


def parse_edit_intent(
    user_message: str,
    available_fields: list[str],
) -> EditIntent:
    """
    ユーザーの発話から編集意図を構造化する（LLM使用）。

    Args:
        user_message: ユーザーの自然言語メッセージ
        available_fields: 編集可能なフィールド名のリスト

    Returns:
        EditIntent（status / field / new_value を含む）
    """
    skill_dir = Path(__file__).parent
    skill_text = load_skill(skill_dir)
    prompt = render_skill(
        skill_text,
        user_message=user_message,
        available_fields="\n".join(f"- {f}" for f in available_fields),
    )

    response_text = call_gemini(prompt)
    cleaned = _extract_json(response_text)

    try:
        result = json.loads(cleaned)
        return EditIntent(
            status=result.get("status", "not_edit"),
            field=result.get("field"),
            new_value=result.get("new_value"),
            clarification_question=result.get("clarification_question"),
            reason=result.get("reason"),
            confidence=float(result.get("confidence", 0.0)),
        )
    except (json.JSONDecodeError, ValueError):
        return EditIntent(status="not_edit", reason="AI応答のパースに失敗しました")


def apply_cell_edit(
    session_id: str,
    field_name: str,
    new_value: str,
    frame_name: str,
    sheet_name: str,
) -> EditResult:
    """
    フィールド名と新値を受け取り、ローカル Excel ファイルのセルを書き換える。

    セル番地は YAML から決定論的に解決する。Firestore・GCS の更新は呼び出し元が行う。

    Args:
        session_id:  セッション ID（Excel ファイル名の特定に使用）
        field_name:  変更するフィールド名
        new_value:   新しい値
        frame_name:  様式名（例: "frameB"）
        sheet_name:  シート名（例: "MRC1"）

    Returns:
        EditResult（書き込んだセル番地と値を含む）

    Raises:
        ValueError:       フィールドが YAML 未定義の場合
        FileNotFoundError: Excel ファイルが存在しない場合
    """
    # 1. YAML でセル番地を解決（決定論的）
    config = load_frame_config(frame_name, sheet_name)
    yaml_cell_defs = extract_cell_definitions(config)

    if field_name not in yaml_cell_defs:
        raise ValueError(f"フィールド '{field_name}' は YAML 定義に存在しません")

    cell_addresses = yaml_cell_defs[field_name]

    # 2. ローカル Excel を読み込み・書き込み・保存
    excel_path = OUTPUT_DIR / f"result_{frame_name}_{session_id}.xlsx"
    if not excel_path.exists():
        raise FileNotFoundError(
            f"セッションの Excel ファイルが見つかりません: {excel_path}"
        )

    workbook = load_workbook_file(str(excel_path))

    written: list[str] = []
    for addr in cell_addresses:
        if write_to_cell(workbook, sheet_name, addr, new_value):
            written.append(addr)

    save_workbook_file(workbook, str(excel_path))

    return EditResult(
        field_name=field_name,
        cell_addresses=written,
        new_value=new_value,
    )


@dataclass
class UnifiedChatResult:
    """統合チャットの結果。Q&A と編集指示の両方を表現する。"""
    type: str                                    # "answer" | "edit" | "ambiguous"
    answer: str | None = None                    # type=="answer" 時の回答文
    field: str | None = None                     # type=="edit" 時の対象フィールド
    new_value: str | None = None                 # type=="edit" 時の新しい値
    clarification_question: str | None = None    # type=="ambiguous" 時の確認質問
    confidence: float = 0.0


def handle_unified_chat(
    user_message: str,
    available_fields: list[str],
    field_name: str = "",
    cell_address: str = "",
    field_value: str = "",
    reasoning: str = "",
) -> UnifiedChatResult:
    """
    ユーザーメッセージを解析し、Q&A応答または編集指示を1回のLLM呼び出しで返す。

    意図判定と応答生成を同時に行うため、Q&A の場合でも余分なLLM呼び出しが発生しない。

    Args:
        user_message:     ユーザーのメッセージ
        available_fields: 編集可能なフィールド名のリスト
        field_name:       現在選択中のフィールド名（Q&A コンテキスト用）
        cell_address:     現在選択中のセル番地
        field_value:      現在選択中のセルの値
        reasoning:        転記時の根拠（source_location 含む）

    Returns:
        UnifiedChatResult（type で Q&A / 編集 / 曖昧を区別）
    """
    skill_dir = Path(__file__).parent
    skill_text = load_skill(skill_dir, skill_name="SKILL_unified.md")

    has_source_info = "抽出元:" in reasoning
    reasoning_display = (
        reasoning if has_source_info
        else "（AIが資料から判断した結果です。具体的な資料の箇所は特定できていません）"
    )

    prompt = render_skill(
        skill_text,
        user_message=user_message,
        available_fields="\n".join(f"- {f}" for f in available_fields),
        field_name=field_name or "（未選択）",
        cell_address=cell_address or "（未選択）",
        field_value=field_value or "（未入力）",
        reasoning=reasoning_display,
    )

    response_text = call_gemini(prompt)
    cleaned = _extract_json(response_text)

    try:
        result = json.loads(cleaned)
        return UnifiedChatResult(
            type=result.get("type", "answer"),
            answer=result.get("answer"),
            field=result.get("field"),
            new_value=result.get("new_value"),
            clarification_question=result.get("clarification_question"),
            confidence=float(result.get("confidence", 0.0)),
        )
    except (json.JSONDecodeError, ValueError):
        # パース失敗時はAI応答をそのまま回答として扱う
        return UnifiedChatResult(type="answer", answer=response_text)


def _extract_json(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return text.strip()
