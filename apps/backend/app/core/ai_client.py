"""
Vertex AI 共通クライアント
"""
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
    )
    if system_instruction:
        config.system_instruction = system_instruction
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config,
    )
    return response.text.strip()