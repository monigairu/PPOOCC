"""Vertex AI 共通クライアント

Gemini API (Vertex AI) との低レイヤー対話を管理する共通モジュール。
確率レイヤーとして、生データ（見積テキスト、画像、PDF等）から型定義に沿った
構造化データを抽出するインターフェースを提供する。
"""

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
from langfuse import observe as langfuse_observe

from apps.backend.app.core.settings import (
    GEMINI_API_TIMEOUT_SEC,
    GEMINI_INLINE_PDF_MAX_BYTES,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
    GEMINI_PDF_MAX_BYTES,
    GEMINI_TRANSFER_LANGFUSE_ENABLED,
)

_client = None
logger = logging.getLogger(__name__)


def _is_prompt_debug_enabled() -> bool:
    """プロンプトデバッグ保存を有効化する条件を判定する。

    判定優先順:
    1) 環境変数 ``NURO_DEBUG`` が truthy (1/true/yes/on)
    2) 起動引数に ``--debug`` を含む
    3) ロガーが DEBUG レベル以上

    Args:
        なし

    Returns:
        bool: 保存有効なら ``True``、無効なら ``False``。

    Example:
        Input:
            os.environ["NURO_DEBUG"] = "true"
            sys.argv = ["uvicorn", "apps.backend.app.api.main:app"]
        Output:
            True
    """
    env_value = os.environ.get("NURO_DEBUG", "").strip().lower()
    if env_value in {"1", "true", "yes", "on"}:
        return True

    argv = {arg.strip().lower() for arg in sys.argv}
    if "--debug" in argv:
        return True

    return logger.isEnabledFor(logging.DEBUG)


def _prompt_debug_artifact_dir() -> Path:
    """プロンプト監査ファイルの保存先ディレクトリを返す。

    Args:
        なし

    Returns:
        Path: リポジトリ基準の ``data/artifacts/debug`` ディレクトリ。

    Example:
        Input:
            なし
        Output:
            Path(".../data/artifacts/debug")
    """
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "data" / "artifacts" / "debug"


def _save_prompt_debug_artifact(*, prompt: str, tag: str, source_file_name: str = "unknown") -> None:
    """NURO_DEBUG 有効時のみ、Gemini 送信プロンプトをテキスト保存する。

    ファイル名は ``<timestamp>_<source_file_name>_<tag>.txt`` 形式で作成し、
    ``source_file_name`` と ``tag`` はファイル名として安全な文字へ正規化する。

    Args:
        prompt: 保存対象のプロンプト本文。
        tag: 用途識別子（例: ``gemini_structured``）。
        source_file_name: 入力資料名。未指定時は ``"unknown"``。

    Returns:
        None: 戻り値は持たない。保存失敗時は warning ログのみ出して処理継続する。

    Example:
        Input:
            prompt = "## 抽出スキーマ\n..."
            tag = "gemini_pdf_structured"
            source_file_name = "estimate.pdf"
        Output:
            なし（副作用として ``data/artifacts/debug`` に
            ``20260701_120000_000000_estimate.pdf_gemini_pdf_structured.txt`` を作成）
    """
    if not _is_prompt_debug_enabled():
        return

    try:
        debug_dir = _prompt_debug_artifact_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        safe_file = re.sub(r"[^A-Za-z0-9,_-]+", "_", source_file_name).strip("_") or "unknown"
        safe_tag = re.sub(r"[^A-Za-z0-9,_-]+", "_", tag).strip("_") or "prompt"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = debug_dir / f"{ts}_{safe_file}_{safe_tag}.txt"
        out_path.write_text(prompt, encoding="utf-8")
        logger.info("[debug][llm] prompt_saved=%s", out_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[debug][llm] prompt save failed: %s", exc)


def _get_client():
    """Gemini API クライアント（シングルトン）を取得する。

    Vertex AI をバックエンドとする `google.genai.Client` インスタンスを生成・保持する。
    この関数は AI クライアントレイヤーの最下部に位置し、すべての Gemini API 呼び出し関数の基盤となる。

    Args:
        なし。

    Returns:
        google.genai.Client: 初期化済みの Gemini API クライアントインスタンス。すべての API 呼び出しに共有される。

    Raises:
        google.genai.errors.APIError: クライアント初期化時に Google API 側で問題が発生した場合。
        ValueError: 環境変数や認証情報が不足している場合。

    Examples:
        >>> client = _get_client()  # doctest: +SKIP

    Note:
        - `_client` グローバル変数を使用した簡易シングルトン実装。スレッドセーフな二重ロック等は行われていない。
        - 常に `vertexai=True` で初期化されるため、GCP Vertex AI エンドポイントをターゲットとする。
    """
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True)
    return _client


def _observe_generation(name: str, *, transfer_path: bool = False):
    """Langfuse の生成トレースを記録するためのデコレータを作成する。

    Gemini API を呼び出す各関数（`call_gemini` や `call_gemini_structured` 等）をラップし、
    入力プロンプト、スキーマ、およびモデルからの応答を Langfuse に送信して可視化・監査を可能にする。

    Args:
        name (str): Langfuse 上に表示されるジェネレーション（生成）の名前。
        transfer_path (bool, optional): データの転記・抽出などの主要な転記パイプライン（転記パス）
            に属する呼び出しであるか。デフォルトは False。True の場合、環境設定の
            `GEMINI_TRANSFER_LANGFUSE_ENABLED` が False であればトレースを行わない。

    Returns:
        Callable: デコレータとして機能する関数。Langfuse 監視が有効な場合は `langfuse_observe`
            でラップされた関数を返し、無効な場合は元の関数をそのまま返す。

    Examples:
        >>> @_observe_generation(name="sample_generation", transfer_path=True)  # doctest: +SKIP
        ... def sample_func(prompt):
        ...     pass

    Note:
        - 開発および本番環境での LLM コスト、応答速度、精度向上のための評価データ収集を担う。
        - 転記パイプライン（`transfer_path=True`）では、大量バッチ処理時のノイズやトークン消費を抑制するため、
          環境変数でトレースの有効・無効を制御可能にしている。
    """
    if transfer_path and not GEMINI_TRANSFER_LANGFUSE_ENABLED:
        return lambda func: func
    return langfuse_observe(name=name, as_type="generation", capture_input=True, capture_output=True)


def _generate_content_with_timeout(*, client, model: str, contents, config):
    """ハードタイムアウト制御付きで Gemini API の `generate_content` を実行する。

    API 呼び出しのハングによるシステム全体のデッドロックを防止するため、指定秒数の
    ハードタイムアウトをバックグラウンドスレッドを使用して強制する。
    タイムアウト値は環境変数 `GEMINI_API_TIMEOUT_SEC` または config 設定から取得される。

    Args:
        client (google.genai.Client): API 実行に使用するクライアントインスタンス。
        model (str): 使用する Gemini モデル名（例: "gemini-2.5-flash"）。
        contents (Any): プロンプト、画像、PDF、またはパーツオブジェクトのリスト。
        config (google.genai.types.GenerateContentConfig): 生成設定（温度、トークン数、スキーマ等）

    Returns:
        google.genai.types.GenerateContentResponse: API からの応答オブジェクト。

    Raises:
        RuntimeError: API 呼び出しが制限時間を超過した場合（タイムアウト）、
            または API から応答が返されなかった場合。
        Exception: API 実行中にエラーが発生した場合、バックグラウンドスレッドで発生した
            例外がメインスレッドに伝播・送出される。

    Examples:
        >>> response = _generate_content_with_timeout(  # doctest: +SKIP
        ...     client=client,
        ...     model="gemini-2.5-flash",
        ...     contents="Hello",
        ...     config=config,
        ... )

    Note:
        - `timeout_sec` が 0 以下の場合は、タイムアウト制御をバイパスしてブロッキング呼び出しを行う。
        - タイムアウト発生時はデーモンスレッドとしてバックグラウンドで処理が継続されるが、
          メインスレッドは `RuntimeError` を受け取って即座にフォールバックや HITL (Human-in-the-loop) へ移行できる。
        - 実運用時の動的調整を考慮し、環境変数 `GEMINI_API_TIMEOUT_SEC` での即時上書きが最優先される。
    """
    # 実運用計測での即時チューニングを可能にするため、環境変数で上書きを許可する。
    timeout_sec = float(os.environ.get("GEMINI_API_TIMEOUT_SEC", str(GEMINI_API_TIMEOUT_SEC)))
    if timeout_sec <= 0:
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    result: dict[str, object] = {}
    error: dict[str, Exception] = {}

    def _invoke() -> None:
        try:
            result["response"] = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            error["exc"] = exc

    thread = threading.Thread(target=_invoke, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)
    if thread.is_alive():
        raise RuntimeError(
            f"Gemini API timeout after {timeout_sec}s (model={model})"
        )
    if "exc" in error:
        raise error["exc"]
    response = result.get("response")
    if response is None:
        raise RuntimeError("Gemini API returned no response")
    return response


@_observe_generation(name="gemini_call")
def call_gemini(prompt, model_name: str | None = None, system_instruction: str = "") -> str:
    """テキスト形式で Gemini に問い合わせ、応答テキストを文字列として返す。

    汎用的なテキスト対話、ドキュメントの要約、または構造化を必要としないメタ推論タスクにおいて、
    Gemini API に直接問い合わせを行う。

    Args:
        prompt (str): 送信するプロンプトテキスト。
        model_name (:obj:`str`, optional): 使用する Gemini モデル名。指定がない場合は
            システム既定のモデル（`GEMINI_MODEL`）が使用される。
        system_instruction (str, optional): モデルのシステム指示子（ペルソナやルール）。
            デフォルトは空文字列。

    Returns:
        str: トリミング（前方・後方スペースの削除）されたモデルのテキスト応答。

    Raises:
        RuntimeError: Gemini の応答が空テキストであった場合（ブロックや打ち切り等の理由を含む）、
            または API がタイムアウトした場合。
        Exception: ネットワークエラーや API 認証エラー。

    Examples:
        >>> call_gemini("日本の首都はどこですか？")  # doctest: +SKIP
        '東京'

    Note:
        - 決定論的な振る舞い（同じ入力に対して常に同じ結果を出力する）を保証するため、
          `temperature` は `0.0` に固定されている。
        - 応答が空テキストの場合は、レスポンスの `finish_reason` を抽出し、
          エラーメッセージに含めて `RuntimeError` を送出する。
    """
    _save_prompt_debug_artifact(
        prompt=prompt,
        tag="gemini_text",
    )
    client = _get_client()
    effective_model = model_name or GEMINI_MODEL
    config = types.GenerateContentConfig(
        temperature=0.0,  # 再現性のため固定（同じ入力で同じ出力を得る）
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        system_instruction=system_instruction or None,
    )
    response = _generate_content_with_timeout(
        client=client,
        model=effective_model,
        contents=prompt,
        config=config,
    )
    if response.text is None:
        finish = _extract_finish_reason(response)
        raise RuntimeError(
            f"Gemini call returned empty text (model={effective_model}, finish_reason={finish})"
        )
    return response.text.strip()


@_observe_generation(name="gemini_structured_call", transfer_path=True)
def call_gemini_structured(
    prompt,
    response_schema: dict,
    model_name: str | None = None,
    system_instruction: str = "",
) -> dict:
    """JSON Schema に従った構造化 JSON 出力を Gemini から取得して辞書オブジェクトとして返す。

    テキスト情報（ドキュメントのテキスト、歩掛の記載等）から、決定論カーネル（Python）で
    安全に処理できる型付き構造化データを抽出する。各ステージ（S1-5等）での中間構造化や、
    表記揺れの吸収を担う。

    Args:
        prompt (str): モデルへの指示およびインプットテキスト。
        response_schema (dict): 期待する出力形式を定義した JSON Schema 形式の辞書。
        model_name (:obj:`str`, optional): 使用する Gemini モデル名。デフォルトはシステム設定値。
        system_instruction (str, optional): システムの動作を制御するシステム指示テキスト。

    Returns:
        dict: JSON Schema に適合するようにパースされた辞書オブジェクト。後続の決定論的な
            計算やルール引き当て処理の入力として使用される。

    Raises:
        RuntimeError: 応答が空テキストで返された場合、または API タイムアウト時。
        json.JSONDecodeError: 万が一、API 応答のテキストが不正な JSON 形式であった場合。

    Examples:
        >>> schema = {  # doctest: +SKIP
        ...     "type": "OBJECT",
        ...     "properties": {
        ...         "item_name": {"type": "STRING"},
        ...         "quantity": {"type": "INTEGER"}
        ...     },
        ...     "required": ["item_name", "quantity"]
        ... }
        >>> call_gemini_structured("配管 5本", schema)  # doctest: +SKIP
        {'item_name': '配管', 'quantity': 5}

    Note:
        - 表記揺れの吸収（例: "350" → "350A" へのノーマライズ）や、テキストからの分類判定を
          確率レイヤー（Gemini）で完結させるための極めて重要なインターフェースである。
        - 既存の `call_gemini` とは完全に独立しており、互換性を維持する。
        - 再現性向上のため、`temperature` は `0.0` に固定される。
    """
    _save_prompt_debug_artifact(
        prompt=prompt,
        tag="gemini_structured",
    )
    client = _get_client()
    effective_model = model_name or GEMINI_MODEL
    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        system_instruction=system_instruction or None,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    response = _generate_content_with_timeout(
        client=client,
        model=effective_model,
        contents=prompt,
        config=config,
    )
    if response.text is None:
        finish = getattr(getattr(response, "candidates", [None])[0], "finish_reason", None)
        raise RuntimeError(
            f"Gemini structured call returned empty text (model={effective_model}, finish_reason={finish})"
        )
    return json.loads(response.text.strip())


@_observe_generation(name="gemini_multimodal_structured_call", transfer_path=True)
def call_gemini_multimodal_structured(
    prompt: str,
    images: list[tuple[str, bytes]],
    response_schema: dict,
    model_name: str | None = None,
    system_instruction: str = "",
) -> dict:
    """画像リストと指示プロンプトを Gemini Multimodal に入力し、JSON Schema 準拠の構造化データを抽出する。

    PDF を画像にレンダリングした結果や、見積書・図面の画像ファイルから、視覚情報を考慮した
    データの構造化を行う。スキャンされた PDF や図面、表形式のレイアウトを極めて正確に認識・抽出する
    マルチモーダルなステージで活用される。

    Args:
        prompt (str): 画像をどのように読み取って構造化するかを示す詳細な指示テキスト。
        images (list[tuple[str, bytes]]): 読み込む画像データのリスト。
            各要素は `(mime_type, image_bytes)` のタプル。
            例: `[("image/png", b"..."), ("image/jpeg", b"...")]`
        response_schema (dict): 抽出データの期待される構造を定義する JSON Schema。
        model_name (:obj:`str`, optional): 使用する Gemini モデル名。指定がない場合は
            システム設定の `GEMINI_MODEL` が適用される。
        system_instruction (str, optional): システム役割のプロンプト定義。

    Returns:
        dict: JSON Schema にしたがってパースされた抽出結果の辞書。
            下流の転記処理、歩掛とのマッチング、または決定論カーネルでの整合検証（R1〜R4）で使用される。

    Raises:
        RuntimeError: 応答テキストが空、あるいはブロックされた場合、または API タイムアウト時。
        json.JSONDecodeError: Gemini から返された応答テキストが不完全または不正な JSON フォーマットである場合。

    Examples:
        >>> images = [("image/png", b"raw_bytes_here")]  # doctest: +SKIP
        >>> schema = {  # doctest: +SKIP
        ...     "type": "OBJECT",
        ...     "properties": {
        ...         "total_amount": {"type": "INTEGER", "description": "見積書に記載されている総額"}
        ...     },
        ...     "required": ["total_amount"]
        ... }
        >>> call_gemini_multimodal_structured("総額を抽出してください", images, schema)  # doctest: +SKIP
        {'total_amount': 1500000}

    Note:
        - スキャンや表のセル結合が多い PDF の場合、PDF の直接的な文字列抽出 (Native PDF) より、
          画像に変換してから本関数を呼び出すマルチモーダル抽出のほうが高い精度が得られる場合がある。
        - 決定論モデルとのインターフェース整合のため、温度 `temperature` は `0.0` に制限される。
    """
    _save_prompt_debug_artifact(
        prompt=prompt,
        tag=f"gemini_multimodal_{len(images)}images",
    )
    client = _get_client()
    effective_model = model_name or GEMINI_MODEL

    parts: list = [
        types.Part.from_bytes(data=img_bytes, mime_type=mime)
        for mime, img_bytes in images
    ]
    parts.append(prompt)

    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        system_instruction=system_instruction or None,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    response = _generate_content_with_timeout(
        client=client,
        model=effective_model,
        contents=parts,
        config=config,
    )
    if response.text is None:
        finish = getattr(getattr(response, "candidates", [None])[0], "finish_reason", None)
        raise RuntimeError(
            f"Gemini multimodal structured call returned empty text "
            f"(model={effective_model}, finish_reason={finish})"
        )
    return json.loads(response.text.strip())