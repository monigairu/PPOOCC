"""
Vertex AI 共通クライアント
"""
from google import genai

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True)
    return _client


def call_gemini(prompt, model_name="gemini-2.5-flash") -> str:
    client = _get_client()
    response = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return response.text.strip()