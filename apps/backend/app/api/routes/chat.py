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
    return f"""あなたはNuRO（廃炉情報管理システム）の様式自動作成AIアシスタントです。
電力会社が提出する廃炉関連の様式（Excelファイル）への転記作業を行い、
その根拠をわかりやすく説明する役割を担っています。

## 今回の転記情報

- フィールド名: {req.field_name}
- セル番地: {req.cell_address}
- 転記した値: {req.field_value}
- 転記時のAI判断根拠: {req.reasoning}

## ユーザーからの質問

{req.message}

## 回答の注意事項

- 上記の転記情報と根拠をもとに、具体的かつ簡潔に回答してください
- 専門用語はわかりやすく補足してください
- 根拠が不明な場合は正直にその旨を伝えてください
- 回答は日本語で行ってください
"""
