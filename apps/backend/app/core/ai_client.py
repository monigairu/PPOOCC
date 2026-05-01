"""
Vertex AI 共通クライアント

Vertex AI（Gemini）への呼び出しを抽象化し、
すべてのエージェントで共通利用できるようにする。
"""
from google import genai

client = genai.Client(vertexai=True)

def call_gemini(prompt, model_name="gemini-2.5-flash") -> str:
    """
    Gemini を呼び出して応答テキストを返す。
    """
    response = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return response.text.strip()