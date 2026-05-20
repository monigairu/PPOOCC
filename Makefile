# NuRO AI Platform 開発用コマンド

.PHONY: backend frontend dev

# バックエンド: apps/ のみ監視（scripts/ の変更でサーバーが再起動しない）
backend:
	uv run uvicorn apps.backend.app.api.main:app \
		--reload \
		--reload-dir apps/backend/app \
		--port 8000

# フロントエンド
frontend:
	cd apps/frontend && npm run dev

# バックエンドとフロントエンドを同時起動（別ターミナルが不要）
dev:
	@echo "バックエンド と フロントエンド を起動します"
	@echo "Ctrl+C で両方停止"
	@(uv run uvicorn apps.backend.app.api.main:app --reload --reload-dir apps/backend/app --port 8000 & \
	  cd apps/frontend && npm run dev)
