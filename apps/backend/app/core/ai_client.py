"""
Vertex AI 共通クライアント
"""
import json

from google import genai
from google.genai import types
from langfuse import observe

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True)
    return _client


@observe(name="gemini_call", as_type="generation", capture_input=True, capture_output=True)
def call_gemini(prompt, model_name="gemini-2.5-flash", system_instruction: str = "") -> str:
    client = _get_client()
    config = types.GenerateContentConfig(
        temperature=0.0,  # 再現性のため固定（同じ入力で同じ出力を得る）
        system_instruction=system_instruction or None,
    )
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config,
    )
    return response.text.strip()


@observe(name="gemini_structured_call", as_type="generation", capture_input=True, capture_output=True)
def call_gemini_structured(
    prompt,
    response_schema: dict,
    model_name: str = "gemini-3.5-flash",
    system_instruction: str = "",
) -> dict:
    """
    structured output を使って JSON dict を返す。
    response_schema（JSON Schema 形式の dict）に従った JSON が返る。
    既存の call_gemini とは独立しており、既存テストに影響しない。
    """
    client = _get_client()
    config = types.GenerateContentConfig(
        temperature=0.0,
        system_instruction=system_instruction or None,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config,
    )
    if response.text is None:
        finish = getattr(getattr(response, "candidates", [None])[0], "finish_reason", None)
        raise RuntimeError(
            f"Gemini structured call returned empty text (model={model_name}, finish_reason={finish})"
        )
    return json.loads(response.text.strip())