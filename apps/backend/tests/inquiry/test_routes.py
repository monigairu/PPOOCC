"""POST /api/inquiry/ask のエンドポイントテスト（DESIGN §4-1・§6）。

pipeline.ask() はモックし、ルート層の責務（入出力スキーマ・棄却は200・
検索障害は502）を検証する。パイプライン自体は test_pipeline.py が担当。
"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.backend.app.api.main import app
from apps.backend.app.inquiry.models import AskResult, Evidence
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    KnowledgeSearchError,
)

client = TestClient(app)

_ANSWERED = AskResult(
    status="answered",
    answer="仮設足場の範囲図と数量明細が必要です [F3#03_KT_1G_01_0004]",
    evidences=[
        Evidence(
            record_id="03_KT_1G_01_0004", sheet="KNI_1G_01",
            snippet="一式計上の内訳…", score=0.9, round=1, message_direction="nuro",
        )
    ],
    grounding_score=0.92,
)

_ABSTAINED = AskResult(
    status="abstained",
    abstain_reason="insufficient_context",
    related=[
        Evidence(
            record_id="03_KT_1G_02_0005", sheet="KNI_1G_02",
            snippet="人件費単価…", round=1, message_direction="denryoku",
        )
    ],
)


class TestAskEndpoint:
    def test_answered_response_matches_design(self):
        """DESIGN §4-1 の回答時レスポンス形（200・evidences・grounding_score）"""
        with patch("apps.backend.app.api.routes.inquiry.ask", return_value=_ANSWERED):
            res = client.post(
                "/api/inquiry/ask",
                json={"question": "1式計上の内訳は？", "utility": "関東電力"},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "answered"
        assert body["grounding_score"] == 0.92
        assert body["evidences"][0]["record_id"] == "03_KT_1G_01_0004"
        assert body["abstain_reason"] is None

    def test_abstained_is_200(self):
        """棄却は正常系＝200（起票に流す。エラーにしない・DESIGN §6）"""
        with patch("apps.backend.app.api.routes.inquiry.ask", return_value=_ABSTAINED):
            res = client.post(
                "/api/inquiry/ask",
                json={"question": "乾式キャスクの費用は？", "utility": "関東電力"},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "abstained"
        assert body["answer"] is None
        assert body["related"][0]["record_id"] == "03_KT_1G_02_0005"

    def test_search_failure_is_502(self):
        """①検索障害は 502（「ナレッジなし」と誤認させない・DESIGN §6/D-14）"""
        with patch(
            "apps.backend.app.api.routes.inquiry.ask",
            side_effect=KnowledgeSearchError("Vertex AI Search エラー"),
        ):
            res = client.post(
                "/api/inquiry/ask",
                json={"question": "q", "utility": "関東電力"},
            )
        assert res.status_code == 502
        assert "ナレッジ検索" in res.json()["detail"]

    def test_validation_empty_question_is_422(self):
        """空の質問・空の会社名はスキーマで弾く（AskRequest・min_length=1）"""
        res = client.post("/api/inquiry/ask", json={"question": "", "utility": "関東電力"})
        assert res.status_code == 422
        res = client.post("/api/inquiry/ask", json={"question": "q", "utility": ""})
        assert res.status_code == 422
