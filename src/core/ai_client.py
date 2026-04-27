"""
Vertex AI 共通クライアント

Vertex AI（Gemini）への呼び出しを抽象化し、
すべてのエージェントで共通利用できるようにする。
"""
import os

import vertexai
from vertexai.generative_models import GenerativeModel


_initialized = False


def _init_vertex_ai() -> None:
    """
    Vertex AI を初期化する。

    すでに初期化済みの場合は何もしない（多重初期化を防ぐ）。
    環境変数 GOOGLE_CLOUD_PROJECT と GOOGLE_CLOUD_LOCATION を使用する。
    """
    global _initialized
    if _initialized:
        return

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    if not project:
        raise ValueError(
            "環境変数 GOOGLE_CLOUD_PROJECT が設定されていません。"
            ".env ファイルを確認してください。"
        )

    vertexai.init(project=project, location=location)
    _initialized = True


def call_gemini(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    """
    Gemini を呼び出して応答テキストを返す。

    Args:
        prompt: 入力プロンプト
        model_name: 使用するモデル名（デフォルト: gemini-2.5-flash）

    Returns:
        Gemini からの応答テキスト
    """
    _init_vertex_ai()
    model = GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text.strip()