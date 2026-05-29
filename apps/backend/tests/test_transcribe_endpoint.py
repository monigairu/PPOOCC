"""
POST /api/transcribe/mrc1 / GET /api/jobs/{job_id} のテスト

Gemini 呼び出しはモックする。
"""
import io
import time
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from apps.backend.app.api.main import app
from apps.backend.app.api.routes.transcribe import job_store


client = TestClient(app)


def _make_xlsx_bytes() -> bytes:
    """最小限の xlsx バイト列を作る（openpyxl でダミーファイル生成）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    wb.active["A1"] = "工事件名"
    wb.active["B1"] = "テスト工事"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── POST /api/transcribe/mrc1 ─────────────────────────────────────────────────

def test_transcribe_returns_job_id():
    """正常なファイルをアップロードすると job_id が返ること"""
    xlsx = _make_xlsx_bytes()
    resp = client.post(
        "/api/transcribe/mrc1",
        files=[("files", ("物量データ.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"sheet": "MRC1", "frame": "frameB"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "accepted"


def test_transcribe_unsupported_format_returns_400():
    """未対応形式（.csv）は 400 を返すこと"""
    resp = client.post(
        "/api/transcribe/mrc1",
        files=[("files", ("data.csv", b"col1,col2\n1,2", "text/csv"))],
        data={"sheet": "MRC1", "frame": "frameB"},
    )
    assert resp.status_code == 400
    assert "未対応" in resp.json()["detail"]


def test_transcribe_multiple_files_accepted():
    """複数ファイルを同時にアップロードできること"""
    xlsx = _make_xlsx_bytes()
    resp = client.post(
        "/api/transcribe/mrc1",
        files=[
            ("files", ("物量データ.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("files", ("工程表.xlsx",   xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ],
        data={"sheet": "MRC1", "frame": "frameB"},
    )
    assert resp.status_code == 200
    assert "job_id" in resp.json()


# ─── GET /api/jobs/{job_id} ────────────────────────────────────────────────────

def test_get_job_status_unknown_returns_404():
    """存在しない job_id は 404 を返すこと"""
    resp = client.get("/api/jobs/nonexistent-job-id")
    assert resp.status_code == 404


def test_get_job_status_running():
    """running 状態のジョブを GET で取得できること"""
    fake_job_id = "test-job-running"
    job_store[fake_job_id] = {"status": "running", "progress": 30, "result": None}

    resp = client.get(f"/api/jobs/{fake_job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["progress"] == 30

    del job_store[fake_job_id]


def test_get_job_status_completed():
    """completed 状態のジョブが result を含むこと"""
    fake_job_id = "test-job-completed"
    job_store[fake_job_id] = {
        "status": "completed",
        "progress": 100,
        "result": {
            "output_path": "output/MRC1_test.xlsx",
            "skipped_cells": ["総額", "全体支払い対象金額"],
            "conflicts": [],
            "formula_results": [],
        },
    }

    resp = client.get(f"/api/jobs/{fake_job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"]["skipped_cells"] == ["総額", "全体支払い対象金額"]

    del job_store[fake_job_id]


def test_get_job_status_failed():
    """failed 状態のジョブが error メッセージを含むこと"""
    fake_job_id = "test-job-failed"
    job_store[fake_job_id] = {
        "status": "failed",
        "progress": 0,
        "error": "Gemini API エラー",
    }

    resp = client.get(f"/api/jobs/{fake_job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "Gemini API エラー" in body["error"]

    del job_store[fake_job_id]


# ─── _collect_skipped_cells ─────────────────────────────────────────────────────

def test_collect_skipped_cells_returns_writable_false_fields():
    """_collect_skipped_cells が writable:false フィールドを返すこと"""
    from apps.backend.app.api.routes.transcribe import _collect_skipped_cells
    skipped = _collect_skipped_cells("frameB", "MRC1")
    assert "総額" in skipped
    assert "全体支払い対象金額" in skipped
    # writable:true（デフォルト）のフィールドは含まれないこと
    assert "工事件名" not in skipped


def test_collect_skipped_cells_nonexistent_frame():
    """存在しないフレームは空リストを返すこと（クラッシュしない）"""
    from apps.backend.app.api.routes.transcribe import _collect_skipped_cells
    result = _collect_skipped_cells("nonexistent_frame", "MRC1")
    assert result == []
