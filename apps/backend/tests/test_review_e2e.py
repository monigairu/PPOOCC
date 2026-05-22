"""
レビュー機能 E2E テスト

テスト対象:
  1. detect_plan_diff       — 計画・実績差分検出ロジック
  2. knowledge_loader       — フィルタリング・権限制御・ファイルなし時の安全動作
  3. POST /api/review       — Firestore + Gemini をモックして全体フローを検証
  4. POST /api/review/{id}/feedback — 承諾・棄却の Firestore 保存制御
  5. GET  /api/review/sessions     — 未レビューセッション一覧

モック方針:
  - Firestore: unittest.mock.MagicMock（接続なし）
  - Gemini:    unittest.mock.patch("apps.backend.app.core.ai_client.call_gemini")
  - Excel:     pandas DataFrame を直接返すように knowledge_loader をパッチ
"""
import json
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from apps.backend.app.agents.reviewer.reviewer_agent import detect_plan_diff, _evaluate_diff, _to_number


# ─────────────────────────────────────────────────────────────────────────────
# 1. detect_plan_diff ユニットテスト
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectPlanDiff:
    """MRC1 様式の計画・実績差分検出"""

    def _make_mappings(self, kubun: str, g18: str = "", k18: str = "") -> list[dict]:
        mappings = [{"field_name": "計画実績区分", "cell_address": "C8", "value": kubun, "reasoning": ""}]
        if g18:
            mappings.append({"field_name": "総額", "cell_address": "G18", "value": g18, "reasoning": ""})
        if k18:
            mappings.append({"field_name": "総額", "cell_address": "K18", "value": k18, "reasoning": ""})
        return mappings

    def test_keikaku_returns_empty(self):
        """計画提出は差分チェックをスキップして空リストを返す"""
        diffs = detect_plan_diff(self._make_mappings("計画", "1000", "1500"))
        assert diffs == []

    def test_jisseki_detects_large_diff(self):
        """実績提出で10%超の乖離を検出する"""
        diffs = detect_plan_diff(self._make_mappings("実績", "1000", "1300"))
        assert len(diffs) == 1
        assert diffs[0]["field_name"] == "総額"
        assert "30.0%" in diffs[0]["diff_note"]

    def test_jisseki_ignores_small_diff(self):
        """実績提出でも10%未満の差異は無視する"""
        diffs = detect_plan_diff(self._make_mappings("実績", "1000", "1050"))
        assert diffs == []

    def test_jisseki_detects_missing_plan(self):
        """計画値が未記入で実績値のみある場合を検出する"""
        diffs = detect_plan_diff(self._make_mappings("実績", "", "1500"))
        assert len(diffs) == 1
        assert "計画値が未記入" in diffs[0]["diff_note"]

    def test_jisseki_detects_missing_actual(self):
        """実績値が未記入で計画値のみある場合を検出する"""
        diffs = detect_plan_diff(self._make_mappings("実績", "1000", ""))
        assert len(diffs) == 1
        assert "実績値が未記入" in diffs[0]["diff_note"]

    def test_both_empty_no_diff(self):
        """計画・実績ともに空のフィールドは差分なしとして無視する"""
        diffs = detect_plan_diff(self._make_mappings("実績", "", ""))
        assert diffs == []

    def test_kubun_missing_returns_empty(self):
        """計画実績区分が mappings にない場合は安全に空リストを返す"""
        diffs = detect_plan_diff([
            {"field_name": "総額", "cell_address": "G18", "value": "1000", "reasoning": ""},
            {"field_name": "総額", "cell_address": "K18", "value": "9999", "reasoning": ""},
        ])
        assert diffs == []


class TestEvaluateDiff:
    """差分評価ロジックのユニットテスト"""

    def test_equal_values_returns_none(self):
        assert _evaluate_diff("1000", "1000") is None

    def test_numeric_below_threshold(self):
        assert _evaluate_diff("1000", "1090") is None  # 9% < 10%

    def test_numeric_above_threshold(self):
        result = _evaluate_diff("1000", "1100")
        assert result is not None
        assert "10.0%" in result

    def test_comma_number(self):
        """カンマ区切り数値を正しく解釈する"""
        result = _evaluate_diff("1,000", "2,000")
        assert result is not None  # 100% 差

    def test_string_diff(self):
        """文字列の差異を検出する"""
        result = _evaluate_diff("計画A", "実績B")
        assert result is not None


class TestToNumber:
    def test_plain(self):
        assert _to_number("1000") == 1000.0

    def test_comma(self):
        assert _to_number("1,000") == 1000.0

    def test_unit(self):
        assert _to_number("1000千円") == 1000.0

    def test_invalid(self):
        assert _to_number("N/A") is None

    def test_empty(self):
        assert _to_number("") is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. knowledge_loader ユニットテスト
# ─────────────────────────────────────────────────────────────────────────────

class TestKnowledgeLoader:
    """Excelファイルなしでも安全に動作することを確認"""

    def test_f2_returns_empty_for_denryoku(self):
        """電力ロールは F2 ナレッジを参照できない"""
        from apps.backend.app.agents.reviewer.knowledge_loader import load_f2
        result = load_f2(caller_role="電力")
        assert result == []

    def test_f2_returns_empty_list_when_no_file(self):
        """F2ファイルが存在しない場合は空リストを返す（エラーにならない）"""
        from apps.backend.app.agents.reviewer.knowledge_loader import load_f2
        result = load_f2(caller_role="NuRO")
        assert isinstance(result, list)

    def test_f3_returns_empty_list_when_no_file(self):
        """F3ファイルが存在しない場合は空リストを返す（エラーにならない）"""
        from apps.backend.app.agents.reviewer.knowledge_loader import load_f3
        result = load_f3(caller_role="NuRO", utility_name=None)
        assert isinstance(result, list)

    def test_f3_denryoku_requires_utility_name(self):
        """電力ロールで utility_name なしは空リストを返す"""
        from apps.backend.app.agents.reviewer.knowledge_loader import load_f3
        result = load_f3(caller_role="電力", utility_name=None)
        assert result == []

    def test_schema_discovery(self):
        """スキーマファイルが正しく検出される（Phase2: _excel_reader に移動）"""
        from apps.backend.app.agents.reviewer._excel_reader import _discover_schemas
        f3 = _discover_schemas("f3")
        f2 = _discover_schemas("f2")
        assert len(f3) == 4, f"F3スキーマは4件のはず: {[s['sheet_name'] for s in f3]}"
        assert len(f2) == 2, f"F2スキーマは2件のはず: {[s['sheet_name'] for s in f2]}"

    def test_f3_schema_sheet_names(self):
        """F3スキーマのsheet_nameが正しい（Phase2: _excel_reader に移動）"""
        from apps.backend.app.agents.reviewer._excel_reader import _discover_schemas
        sheets = {s["sheet_name"] for s in _discover_schemas("f3")}
        assert sheets == {"KNI_1G_01", "KNI_1G_02", "KNI_1G_03", "KNI_2G"}

    def test_excel_reader_with_mock_data(self):
        """モックDataFrameでExcel読み込み処理を検証する（Phase2: _excel_reader に移動）"""
        import pandas as pd
        from apps.backend.app.agents.reviewer._excel_reader import _read_excel_by_schema

        schema = {
            "layout": {"data_start_row": 1},
            "loader_config": {"id_column": "A"},
            "fixed_columns": [
                {"key": "id",            "col": "A", "dtype": "string"},
                {"key": "cost_category", "col": "B", "dtype": "string"},
            ],
            "repeating_qa_columns": {
                "start_col": "C", "col_per_round": 2, "max_rounds": 1,
                "fields": [
                    {"key": "nuro_comment",  "col_offset": 0},
                    {"key": "denryoku_reply","col_offset": 1},
                ],
            },
            "output_model": {"flatten_qa": True},
            "meta_cells": {},
        }

        raw_df = pd.DataFrame([
            ["03_1G_01_0001", "維持管理費", "確認します", "問題ありません"],
        ])

        with patch("apps.backend.app.agents.reviewer._excel_reader.pd.read_excel", return_value=raw_df):
            from pathlib import Path
            records, utility = _read_excel_by_schema(schema, Path("dummy.xlsx"))

        assert utility == ""
        assert len(records) == 2
        assert records[0]["id"] == "03_1G_01_0001"
        assert records[0]["message_direction"] == "nuro"
        assert records[1]["message_direction"] == "denryoku"

    def test_supplement_returns_empty_for_denryoku(self):
        """電力ロールは補足資料を参照できない（Phase 3）"""
        from apps.backend.app.agents.reviewer.knowledge_loader import load_supplement
        result = load_supplement(caller_role="電力")
        assert result == []

    def test_supplement_returns_empty_when_datastore_not_configured(self):
        """データストアID未設定時は空リストにフォールバックする（Phase 3）"""
        import apps.backend.app.agents.reviewer.knowledge_loader as kl
        original = kl.VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID
        try:
            kl.VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID = ""
            result = kl.load_supplement(caller_role="NuRO")
            assert result == []
        finally:
            kl.VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID = original


# ─────────────────────────────────────────────────────────────────────────────
# 3. POST /api/review エンドポイント E2E テスト
# ─────────────────────────────────────────────────────────────────────────────

MOCK_MAPPINGS = [
    {"field_name": "計画実績区分", "cell_address": "C8",  "value": "実績",           "reasoning": ""},
    {"field_name": "電力会社",     "cell_address": "C5",  "value": "AA電力株式会社", "reasoning": ""},
    {"field_name": "総額",         "cell_address": "G18", "value": "1000",           "reasoning": ""},
    {"field_name": "総額",         "cell_address": "K18", "value": "1500",           "reasoning": ""},
]

MOCK_GEMINI_RESPONSE = json.dumps({
    "review_items": [
        {
            "field_name": "総額",
            "cell_address": "K18",
            "severity": "要確認",
            "comment": "計画値と実績値の乖離が50%です",
            "evidence": "計画差分: G18=1000, K18=1500",
            "knowledge_source": "計画差分",
        }
    ],
    "summary": "総額に大きな乖離があります。",
})


def _make_mock_firestore(mappings: list[dict] = MOCK_MAPPINGS, reviewed: bool = False):
    """Firestoreクライアントのモックを生成する"""
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {
        "session_id": "test-session-001",
        "utility_name": "AA電力",
        "frame_name": "frameB",
        "sheet_name": "MRC1",
        "mappings": mappings,
        "reviewed": reviewed,
    }
    db = MagicMock()
    db.collection.return_value.document.return_value.get.return_value = doc
    db.collection.return_value.document.return_value.collection.return_value.document.return_value.set = MagicMock()
    db.collection.return_value.document.return_value.update = MagicMock()
    return db


_REVIEW_FS_PATH   = "apps.backend.app.api.routes.review.get_firestore_client"
_UPLOAD_FS_PATH   = "apps.backend.app.api.routes.upload.get_firestore_client"
# ADK 移行後、call_gemini は adk/agents.py 内で使われるためモック先を変更
_GEMINI_PATH      = "apps.backend.app.agents.reviewer.adk.agents.call_gemini"


@pytest.fixture
def client():
    """FastAPI テストクライアント（Firestore・Gemini をモック）"""
    # review.py が直接参照する名前をパッチする（呼び出し元モジュールへのパッチが必要）
    with patch(_REVIEW_FS_PATH, return_value=_make_mock_firestore()), \
         patch(_UPLOAD_FS_PATH, return_value=MagicMock()), \
         patch(_GEMINI_PATH, return_value=MOCK_GEMINI_RESPONSE):
        from apps.backend.app.api.main import app
        yield TestClient(app)


class TestReviewEndpoint:

    def test_review_success(self, client):
        """正常系: レビュー結果と mappings が返る"""
        res = client.post("/api/review", json={
            "session_id": "test-session-001",
            "utility_name": "AA電力",
            "sheet_name": "MRC1",
            "frame_name": "frameB",
        })
        assert res.status_code == 200
        data = res.json()
        assert "review_id" in data
        assert isinstance(data["review_items"], list)
        assert len(data["review_items"]) >= 1
        # mappings がレスポンスに含まれる（グリッド表示修正）
        assert "mappings" in data
        assert len(data["mappings"]) == len(MOCK_MAPPINGS)

    def test_review_session_not_found(self):
        """セッション不存在は 404 を返す"""
        doc = MagicMock()
        doc.exists = False
        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = doc

        with patch(_REVIEW_FS_PATH, return_value=db), \
             patch(_GEMINI_PATH, return_value=MOCK_GEMINI_RESPONSE):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.post("/api/review", json={
                "session_id": "no-such-session",
                "utility_name": "AA電力",
            })
        assert res.status_code == 404

    def test_review_empty_mappings(self):
        """mappings が空のセッションは 400 を返す"""
        db = _make_mock_firestore(mappings=[])
        with patch(_REVIEW_FS_PATH, return_value=db), \
             patch(_GEMINI_PATH, return_value=MOCK_GEMINI_RESPONSE):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.post("/api/review", json={
                "session_id": "empty-session",
                "utility_name": "AA電力",
            })
        assert res.status_code == 400

    def test_review_response_includes_severity(self, client):
        """指摘の severity が ReviewItem に含まれる"""
        res = client.post("/api/review", json={
            "session_id": "test-session-001",
            "utility_name": "AA電力",
        })
        assert res.status_code == 200
        items = res.json()["review_items"]
        for item in items:
            assert item["severity"] in ("要確認", "AIからの指摘")


# ─────────────────────────────────────────────────────────────────────────────
# 4. POST /api/review/{id}/feedback エンドポイント テスト
# ─────────────────────────────────────────────────────────────────────────────

def _make_fs_with_review():
    """フィードバック用Firestoreモック（毎回フレッシュなmockを返す）"""
    review_doc = MagicMock()
    review_doc.reference.update = MagicMock()
    db = MagicMock()
    db.collection_group.return_value.where.return_value.limit.return_value.stream.return_value = iter([review_doc])
    return db, review_doc


class TestFeedbackEndpoint:

    def test_accept_returns_saved(self):
        """承諾（accept）は "saved" を返し Firestore に書き込む"""
        db, review_doc = _make_fs_with_review()
        with patch(_REVIEW_FS_PATH, return_value=db):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.post("/api/review/rev-001/feedback", json={
                "item_id": "review_001",
                "decision": "accept",
                "comment": "",
            })
        assert res.status_code == 200
        assert res.json()["status"] == "saved"
        review_doc.reference.update.assert_called_once()

    def test_reject_returns_discarded_and_increments_decided_count(self):
        """棄却（reject）は "discarded" を返し decided_count をインクリメントする"""
        review_doc = MagicMock()
        review_doc.reference.update = MagicMock()
        db = MagicMock()
        db.collection_group.return_value.where.return_value.limit.return_value.stream.return_value = iter([review_doc])
        with patch(_REVIEW_FS_PATH, return_value=db):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.post("/api/review/rev-001/feedback", json={
                "item_id": "review_001",
                "decision": "reject",
                "comment": "不要と判断",
            })
        assert res.status_code == 200
        assert res.json()["status"] == "discarded"
        # 棄却は decided_count のみインクリメント（指摘内容は保存しない）
        review_doc.reference.update.assert_called_once()

    def test_invalid_decision_returns_400(self):
        """無効な decision 値は 400 を返す"""
        db = MagicMock()
        with patch(_REVIEW_FS_PATH, return_value=db):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.post("/api/review/rev-001/feedback", json={
                "item_id": "review_001",
                "decision": "maybe",
            })
        assert res.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /api/review/sessions エンドポイント テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionsEndpoint:

    def test_returns_unreviewed_sessions(self):
        """未レビューセッションの一覧が返る"""
        from datetime import datetime, timezone

        mock_doc = MagicMock()
        mock_doc.id = "sess-001"
        mock_doc.to_dict.return_value = {
            "session_id": "sess-001",
            "utility_name": "AA電力",
            "frame_name": "frameB",
            "sheet_name": "MRC1",
            "created_at": datetime(2026, 5, 15, tzinfo=timezone.utc),
            "reviewed": False,
        }

        db = MagicMock()
        db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = iter([mock_doc])

        with patch(_REVIEW_FS_PATH, return_value=db):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.get("/api/review/sessions")

        assert res.status_code == 200
        sessions = res.json()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-001"
        assert sessions[0]["utility_name"] == "AA電力"
        assert sessions[0]["reviewed"] is False

    def test_returns_empty_when_no_sessions(self):
        """セッションがない場合は空リストを返す"""
        db = MagicMock()
        db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = iter([])

        with patch(_REVIEW_FS_PATH, return_value=db):
            from apps.backend.app.api.main import app
            c = TestClient(app)
            res = c.get("/api/review/sessions")

        assert res.status_code == 200
        assert res.json() == []
