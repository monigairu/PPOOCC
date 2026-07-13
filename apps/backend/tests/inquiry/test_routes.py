"""/api/inquiry 系エンドポイントのテスト（DESIGN §4-1・§6）。

pipeline.ask() / store.* はモックし、ルート層の責務（入出力スキーマ・
ステータスコード変換：棄却=200／404／409／502）を検証する。
パイプラインは test_pipeline.py、永続化は test_store.py が担当。
"""
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.api_core.exceptions import GoogleAPIError

from apps.backend.app.api.main import app
from apps.backend.app.inquiry.models import AskResult, Evidence, Inquiry
from apps.backend.app.inquiry.store import (
    InquiryNotFoundError,
    InvalidTransitionError,
)
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    KnowledgeSearchError,
)

client = TestClient(app)

_STORE = "apps.backend.app.api.routes.inquiry.store"

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


# ── (b) 起票管理（フェーズ2）─────────────────────────────────────────────────

_NOW = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)

_INQUIRY = Inquiry(
    inquiry_id="abc123",
    number="0001",
    category="質問",
    content="〇〇タンクは支払い対象でしょうか",
    requester="関東電力 太郎",
    status="open",
    created_at=_NOW,
    updated_at=_NOW,
    self_solve_log=_ABSTAINED,
)

_FILE_BODY = {
    "category": "質問",
    "content": "〇〇タンクは支払い対象でしょうか",
    "requester": "関東電力 太郎",
}


class TestCreateInquiryEndpoint:
    def test_create_returns_201_with_number(self):
        """起票は 201 で {inquiry_id, number} を返す（DESIGN §4-1）"""
        with patch(f"{_STORE}.create_inquiry", return_value=_INQUIRY) as create:
            body = {**_FILE_BODY, "self_solve_log": _ABSTAINED.model_dump()}
            res = client.post("/api/inquiry", json=body)
        assert res.status_code == 201
        assert res.json() == {"inquiry_id": "abc123", "number": "0001"}
        # 棄却→起票導線の self_solve_log がそのまま store に渡ること（§4-2）
        passed = create.call_args.args[0]
        assert passed.self_solve_log.abstain_reason == "insufficient_context"

    def test_create_empty_content_is_422(self):
        res = client.post("/api/inquiry", json={**_FILE_BODY, "content": ""})
        assert res.status_code == 422

    def test_firestore_outage_is_502(self):
        """Firestore 障害は 502（起票消失を隠さない・DESIGN §6）"""
        with patch(f"{_STORE}.create_inquiry", side_effect=GoogleAPIError("unavailable")):
            res = client.post("/api/inquiry", json=_FILE_BODY)
        assert res.status_code == 502
        assert "問い合わせデータベース" in res.json()["detail"]


class TestListAndGetEndpoints:
    def test_list_passes_requester_filter(self):
        """?requester= が store にそのまま渡る（電力=自分の分・§4-1）"""
        with patch(f"{_STORE}.list_inquiries", return_value=[_INQUIRY]) as lst:
            res = client.get("/api/inquiry", params={"requester": "関東電力 太郎"})
        assert res.status_code == 200
        assert res.json()[0]["number"] == "0001"
        assert lst.call_args.kwargs == {"requester": "関東電力 太郎"}

    def test_list_without_filter_is_all(self):
        """無指定は全件（NuRO向け・§4-1）"""
        with patch(f"{_STORE}.list_inquiries", return_value=[]) as lst:
            res = client.get("/api/inquiry")
        assert res.status_code == 200
        assert lst.call_args.kwargs == {"requester": None}

    def test_get_returns_inquiry(self):
        with patch(f"{_STORE}.get_inquiry", return_value=_INQUIRY):
            res = client.get("/api/inquiry/abc123")
        assert res.status_code == 200
        assert res.json()["inquiry_id"] == "abc123"
        assert res.json()["self_solve_log"]["status"] == "abstained"

    def test_get_missing_is_404(self):
        with patch(f"{_STORE}.get_inquiry", side_effect=InquiryNotFoundError("x")):
            res = client.get("/api/inquiry/no-such-id")
        assert res.status_code == 404


class TestAnswerEndpoint:
    def test_answer_returns_204(self):
        with patch(f"{_STORE}.save_answer") as save:
            res = client.post(
                "/api/inquiry/abc123/answer",
                json={"content": "内訳明細が必要です", "answered_by": "NuRO 担当"},
            )
        assert res.status_code == 204
        assert save.call_args.args[0] == "abc123"

    def test_answer_conflict_is_409(self):
        """answered への再回答は 409（§1-3 外の遷移・D-15）"""
        with patch(
            f"{_STORE}.save_answer",
            side_effect=InvalidTransitionError("answered", "answered"),
        ):
            res = client.post(
                "/api/inquiry/abc123/answer",
                json={"content": "c", "answered_by": "n"},
            )
        assert res.status_code == 409

    def test_answer_empty_content_is_422(self):
        res = client.post(
            "/api/inquiry/abc123/answer", json={"content": "", "answered_by": "n"}
        )
        assert res.status_code == 422


class TestStatusEndpoint:
    def test_resolve_returns_204(self):
        with patch(f"{_STORE}.update_status") as update:
            res = client.patch("/api/inquiry/abc123/status", json={"status": "resolved"})
        assert res.status_code == 204
        assert update.call_args.args == ("abc123", "resolved")

    def test_status_answered_is_422(self):
        """"answered" はスキーマで弾く（open→answered は /answer 専用・D-15）"""
        res = client.patch("/api/inquiry/abc123/status", json={"status": "answered"})
        assert res.status_code == 422

    def test_invalid_transition_is_409(self):
        with patch(
            f"{_STORE}.update_status",
            side_effect=InvalidTransitionError("open", "resolved"),
        ):
            res = client.patch("/api/inquiry/abc123/status", json={"status": "resolved"})
        assert res.status_code == 409
        assert "open" in res.json()["detail"]
