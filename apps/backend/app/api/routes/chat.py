"""
POST /api/chat

セルの根拠についてAIと会話するエンドポイント。
転記時の根拠データをコンテキストとしてGeminiに渡し、
ユーザーの質問に答えさせる。
"""
from fastapi import APIRouter

from apps.backend.app.api.models import ChatRequest, ChatResponse
from apps.backend.app.core.ai_client import call_gemini

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    セルの根拠についてAIと会話する。

    転記時の根拠（reasoning）をコンテキストとして渡すことで、
    AIが「なぜそのセルに値を入れたか」を説明できる。
    """
    prompt = _build_prompt(request)
    answer = call_gemini(prompt)

    return ChatResponse(answer=answer)


def _build_prompt(req: ChatRequest) -> str:
    """
    チャット用プロンプトを構築する。

    転記時の根拠をコンテキストとして含めることで、
    AIが具体的な説明をできるようにする。
    """
    # reasoningにキャッシュ関連の文言が含まれているか判定
    is_cache_reasoning = "キャッシュ" in req.reasoning

    reasoning_context = (
        "（この転記はAIが資料を分析した結果です。具体的な資料の箇所は特定できていません）"
        if is_cache_reasoning
        else req.reasoning
    )

    return f"""あなたはNuRO（廃炉情報管理システム）の様式自動作成AIアシスタントです。
電力会社が提出する廃炉関連の様式（Excelファイル）への転記作業を行い、
その根拠をわかりやすく説明する役割を担っています。

## 今回の転記情報

- フィールド名: {req.field_name}
- セル番地: {req.cell_address}
- 転記した値: {req.field_value}
- 転記根拠: {reasoning_context}

## ユーザーからの質問

{req.message}

## 回答の注意事項

- 上記の転記情報と根拠をもとに、具体的かつ簡潔に回答してください
- 「キャッシュ」「キャッシュから取得」などシステム内部の用語は絶対に使わないこと
- 根拠に具体的な資料名・ページ・セクションが含まれている場合はそれを引用して説明すること
- 根拠が不明・特定できない場合は「この値はAIが資料から判断した結果です。
  具体的な資料名や箇所についてはアップロードされた資料を直接ご確認ください」と案内すること
- 専門用語はわかりやすく補足してください
- 回答は日本語で行ってください
"""
