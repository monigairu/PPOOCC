"""
FastAPI アプリケーション本体

起動方法:
    uv run uvicorn apps.backend.app.api.main:app --reload --port 8000

アクセス:
    API:    http://localhost:8000/api/
    ドキュメント: http://localhost:8000/docs
"""
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.backend.app.api.routes import upload, chat, template, review, sessions
from apps.backend.app.core.settings import CORS_ORIGINS

# .envファイルの読み込み（Vertex AI認証情報など）
load_dotenv()

# ── FastAPIアプリの初期化 ──────────────────────
app = FastAPI(
    title="NuRO 様式自動作成 API",
    description="電力会社の資料をNuRO様式（フレームB）に自動転記するAPIです。",
    version="0.1.0",
)

# ── CORS設定 ──────────────────────────────────
# 許可オリジンは settings.py で管理（本番では環境変数から読む）
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# ── ルーターの登録 ─────────────────────────────
app.include_router(upload.router,   prefix="/api", tags=["転記"])
app.include_router(chat.router,    prefix="/api", tags=["チャット"])
app.include_router(template.router, prefix="/api", tags=["テンプレート"])
app.include_router(review.router,   prefix="/api", tags=["レビュー"])
app.include_router(sessions.router, prefix="/api", tags=["セッション"])


# ── ヘルスチェック ─────────────────────────────
@app.get("/")
async def root():
    """サーバーが起動しているか確認するエンドポイント。"""
    return {"status": "ok", "message": "NuRO 様式自動作成 API が起動しています"}
