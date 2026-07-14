"""store.py（Firestore アクセス層）のテスト（DESIGN §3-5・§4-2・D-15）。

実 Firestore には接続せず、インメモリのフェイクで検証する：
    - get_firestore_client をフェイクDBに差し替え
    - _run_in_transaction は「fn を即時実行」に差し替え（トランザクション機構自体は
      google-cloud-firestore の責務であり、ここでは採番・遷移ルールの正しさを見る）

検証の中心は契約の3点：採番（number 連番）・状態遷移（§1-3 外は
InvalidTransitionError）・不存在（InquiryNotFoundError）。
"""
import uuid
from datetime import datetime, timezone

import pytest

from apps.backend.app.inquiry import store
from apps.backend.app.inquiry.models import AnswerCreate, AskResult, InquiryCreate


# ── フェイク Firestore（store.py が使う最小限の表面のみ）────────────────────

class FakeSnapshot:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, db: "FakeFirestore", collection: str, doc_id: str):
        self._db = db
        self._collection = collection
        self.id = doc_id

    def get(self, transaction=None) -> FakeSnapshot:
        data = self._db.data.get(self._collection, {}).get(self.id)
        return FakeSnapshot(self.id, data)


class FakeCollection:
    def __init__(self, db: "FakeFirestore", name: str):
        self._db = db
        self._name = name

    def document(self, doc_id: str | None = None) -> FakeDocRef:
        return FakeDocRef(self._db, self._name, doc_id or uuid.uuid4().hex)

    def stream(self):
        for doc_id, data in self._db.data.get(self._name, {}).items():
            yield FakeSnapshot(doc_id, data)


class FakeTransaction:
    """set/update を即時書き込みするフェイク（原子性はテスト対象外）。"""

    def __init__(self, db: "FakeFirestore"):
        self._db = db

    def set(self, ref: FakeDocRef, data: dict) -> None:
        self._db.data.setdefault(ref._collection, {})[ref.id] = dict(data)

    def update(self, ref: FakeDocRef, data: dict) -> None:
        self._db.data[ref._collection][ref.id].update(data)


class FakeFirestore:
    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)


@pytest.fixture
def fake_db(monkeypatch) -> FakeFirestore:
    db = FakeFirestore()
    monkeypatch.setattr(store, "get_firestore_client", lambda: db)
    monkeypatch.setattr(store, "_run_in_transaction", lambda fn: fn(FakeTransaction(db)))
    return db


def _create(requester: str = "関東電力 太郎", **overrides) -> str:
    params = {"category": "質問", "content": "〇〇タンクは支払い対象でしょうか", "requester": requester}
    params.update(overrides)
    return store.create_inquiry(InquiryCreate(**params)).inquiry_id


_ABSTAINED = AskResult(status="abstained", abstain_reason="insufficient_context")


# ── create / get / list ──────────────────────────────────────────────────────

class TestCreateInquiry:
    def test_create_persists_open_inquiry(self, fake_db):
        """起票直後は status=open・入力値と self_solve_log が保存される（§4-2）"""
        inquiry_id = _create(self_solve_log=_ABSTAINED)
        saved = store.get_inquiry(inquiry_id)
        assert saved.inquiry_id == inquiry_id
        assert saved.status == "open"
        assert saved.requester == "関東電力 太郎"
        assert saved.self_solve_log.abstain_reason == "insufficient_context"
        assert saved.answer is None and saved.ai_draft is None

    def test_create_returns_saved_doc_without_reread(self, fake_db):
        """返り値は保存済み文書そのもの（番号取得のための再読取をさせない・§3-5）"""
        created = store.create_inquiry(
            InquiryCreate(category="質問", content="c", requester="関東電力 太郎")
        )
        assert created.number == "0001"
        assert created == store.get_inquiry(created.inquiry_id)

    def test_number_increments_per_creation(self, fake_db):
        """number はカウンタ採番で "0001" から連番（§4-2）"""
        first = store.get_inquiry(_create())
        second = store.get_inquiry(_create())
        assert (first.number, second.number) == ("0001", "0002")
        # カウンタは inquiries とは別コレクション（一覧クエリに混ざらない・§3-5）
        assert store.COUNTER_COLLECTION in fake_db.data
        assert len(fake_db.data[store.INQUIRY_FIRESTORE_COLLECTION]) == 2


class TestGetAndList:
    def test_get_missing_raises_not_found(self, fake_db):
        with pytest.raises(store.InquiryNotFoundError):
            store.get_inquiry("no-such-id")

    def test_list_filters_by_requester(self, fake_db):
        """requester 指定=自分の分のみ（電力）／無指定=全件（NuRO）（§3-5）"""
        _create(requester="関東電力 太郎")
        _create(requester="関東電力 太郎")
        _create(requester="北の海電力 花子")
        assert len(store.list_inquiries()) == 3
        mine = store.list_inquiries(requester="関東電力 太郎")
        assert len(mine) == 2
        assert all(inq.requester == "関東電力 太郎" for inq in mine)

    def test_list_orders_by_updated_at_desc(self, fake_db):
        """一覧は updated_at 降順（動きのあった問い合わせが先頭・§4-1）"""
        first_id = _create()
        _create()
        store.save_answer(first_id, AnswerCreate(content="回答", answered_by="NuRO 担当"))
        assert [inq.inquiry_id for inq in store.list_inquiries()][0] == first_id


# ── 状態遷移（§1-3・D-15）────────────────────────────────────────────────────

class TestSaveAnswer:
    def test_answer_transitions_open_to_answered(self, fake_db):
        inquiry_id = _create()
        store.save_answer(inquiry_id, AnswerCreate(content="内訳明細が必要です", answered_by="NuRO 担当"))
        saved = store.get_inquiry(inquiry_id)
        assert saved.status == "answered"
        assert saved.answer.content == "内訳明細が必要です"
        assert saved.answer.answered_at.tzinfo is not None  # tz-aware（models docstring）

    def test_answer_twice_is_invalid_transition(self, fake_db):
        """answered への再回答は 409 相当（差し戻しで open に戻してから）"""
        inquiry_id = _create()
        answer = AnswerCreate(content="回答", answered_by="NuRO 担当")
        store.save_answer(inquiry_id, answer)
        with pytest.raises(store.InvalidTransitionError):
            store.save_answer(inquiry_id, answer)

    def test_answer_missing_raises_not_found(self, fake_db):
        with pytest.raises(store.InquiryNotFoundError):
            store.save_answer("no-such-id", AnswerCreate(content="c", answered_by="n"))


class TestUpdateStatus:
    def test_resolve_and_reopen_from_answered(self, fake_db):
        """§1-3 の電力側遷移：answered→resolved（解決）／answered→open（差し戻し）"""
        inquiry_id = _create()
        store.save_answer(inquiry_id, AnswerCreate(content="回答", answered_by="NuRO 担当"))
        store.update_status(inquiry_id, "resolved")
        assert store.get_inquiry(inquiry_id).status == "resolved"

        reopen_id = _create()
        store.save_answer(reopen_id, AnswerCreate(content="回答", answered_by="NuRO 担当"))
        store.update_status(reopen_id, "open")
        assert store.get_inquiry(reopen_id).status == "open"

    @pytest.mark.parametrize("requested", ["resolved", "answered"])
    def test_transitions_from_open_are_rejected(self, fake_db, requested):
        """open からの遷移は不可（open→answered は save_answer 専用・D-15）"""
        inquiry_id = _create()
        with pytest.raises(store.InvalidTransitionError):
            store.update_status(inquiry_id, requested)

    def test_missing_raises_not_found(self, fake_db):
        with pytest.raises(store.InquiryNotFoundError):
            store.update_status("no-such-id", "resolved")


# ── AIドラフト（フェーズ3の保存口だけ先行実装）──────────────────────────────

class TestSaveDraft:
    def test_draft_saved_and_overwritten(self, fake_db):
        inquiry_id = _create()
        store.save_draft(inquiry_id, _ABSTAINED)
        answered = AskResult(status="answered", answer="回答案", grounding_score=0.9)
        store.save_draft(inquiry_id, answered)  # 再生成で上書き（§4-2）
        assert store.get_inquiry(inquiry_id).ai_draft.answer == "回答案"

    def test_draft_missing_raises_not_found(self, fake_db):
        with pytest.raises(store.InquiryNotFoundError):
            store.save_draft("no-such-id", _ABSTAINED)
