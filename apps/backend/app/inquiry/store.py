"""問い合わせ（inquiries コレクション）の Firestore アクセス層（DESIGN §3-5）。

起票(b)のデータ永続化を一手に担う。ルート層（api/routes/inquiry.py）は
本モジュールの関数と例外だけを見ればよく、Firestore の構造・遷移ルールを知らない。

契約（DESIGN §3-5・§4-2）:
    - 文書スキーマは models.Inquiry と同型（保存時に Pydantic で検証済みの値のみ書く）。
    - number の採番は inquiry_counters/{コレクション名} のカウンタをトランザクションで
      インクリメント（一覧クエリにカウンタ文書が混ざらないよう別コレクション）。
    - 状態遷移（§1-3）の検証は本モジュールに集約（D-15）。違反は InvalidTransitionError
      を送出し、ルート層が 409 に変換する。open→answered は save_answer のみが行う
      （「回答なしの answered」を作らせない）。
    - タイムスタンプは tz-aware な datetime.now(timezone.utc)（SERVER_TIMESTAMP 不使用・
      models.Inquiry docstring 参照）。
"""
import logging
from datetime import datetime, timezone

from google.cloud import firestore

from apps.backend.app.core.firestore_client import get_firestore_client
from apps.backend.app.inquiry.config import INQUIRY_FIRESTORE_COLLECTION
from apps.backend.app.inquiry.models import (
    AnswerCreate,
    AskResult,
    Inquiry,
    InquiryAnswer,
    InquiryCreate,
    InquiryStatus,
)

logger = logging.getLogger(__name__)

# 採番カウンタの置き場。文書IDにコレクション名を使い、E2E用の別コレクション
# （INQUIRY_FIRESTORE_COLLECTION 切替時）でも採番が衝突しない
COUNTER_COLLECTION = "inquiry_counters"

# update_status が受け付ける遷移（現status, 要求status）。§1-3 の電力側遷移のみ（D-15）
_ALLOWED_STATUS_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {("answered", "resolved"), ("answered", "open")}
)


class InquiryNotFoundError(Exception):
    """指定 inquiry_id の文書が存在しない。ルート層で 404 に変換する。"""


class InvalidTransitionError(Exception):
    """§1-3 に無い状態遷移の要求。ルート層で 409 に変換する。"""

    def __init__(self, current: str, requested: str):
        self.current = current
        self.requested = requested
        super().__init__(f"状態遷移できません: {current} → {requested}")


def _collection() -> firestore.CollectionReference:
    return get_firestore_client().collection(INQUIRY_FIRESTORE_COLLECTION)


def _run_in_transaction(fn):
    """fn(transaction) をトランザクション実行する（テストで差し替える継ぎ目）。"""
    transaction = get_firestore_client().transaction()
    return firestore.transactional(fn)(transaction)


def _get_in_transaction(doc_ref, transaction) -> dict:
    """トランザクション内で文書を読み、無ければ InquiryNotFoundError。"""
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise InquiryNotFoundError(doc_ref.id)
    return snapshot.to_dict()


def create_inquiry(inquiry: InquiryCreate) -> Inquiry:
    """採番して保存し、保存済み文書（Inquiry）を返す。

    書込後の再読取をさせないため ID ではなく文書全体を返す（番号取得のための
    追加読取が失敗すると「保存成功なのに 502」→リトライで重複起票になるため）。
    """
    counter_ref = (
        get_firestore_client()
        .collection(COUNTER_COLLECTION)
        .document(INQUIRY_FIRESTORE_COLLECTION)
    )
    doc_ref = _collection().document()  # Firestore 自動採番ID

    def _create(transaction) -> Inquiry:
        snapshot = counter_ref.get(transaction=transaction)
        next_count = (snapshot.to_dict() or {}).get("count", 0) + 1
        now = datetime.now(timezone.utc)
        doc = Inquiry(
            inquiry_id=doc_ref.id,
            number=f"{next_count:04d}",
            category=inquiry.category,
            content=inquiry.content,
            requester=inquiry.requester,
            status="open",
            created_at=now,
            updated_at=now,
            self_solve_log=inquiry.self_solve_log,
        )
        transaction.set(counter_ref, {"count": next_count})
        transaction.set(doc_ref, doc.model_dump())
        return doc

    created = _run_in_transaction(_create)
    logger.info(
        "問い合わせを起票: inquiry_id=%s requester=%s", created.inquiry_id, inquiry.requester
    )
    return created


def list_inquiries(*, requester: str | None = None) -> list[Inquiry]:
    """一覧を updated_at 降順で返す。requester 指定時は自分の分のみ（電力）、無指定は全件（NuRO）。

    絞り込み・並べ替えはクライアント側で行う（Firestore の複合インデックス不要化。
    PoC の件数規模では全件取得で十分）。
    """
    inquiries = [Inquiry(**doc.to_dict()) for doc in _collection().stream()]
    if requester is not None:
        inquiries = [inq for inq in inquiries if inq.requester == requester]
    return sorted(inquiries, key=lambda inq: inq.updated_at, reverse=True)


def get_inquiry(inquiry_id: str) -> Inquiry:
    """詳細1件。無ければ InquiryNotFoundError。"""
    snapshot = _collection().document(inquiry_id).get()
    if not snapshot.exists:
        raise InquiryNotFoundError(inquiry_id)
    return Inquiry(**snapshot.to_dict())


def save_answer(inquiry_id: str, answer: AnswerCreate) -> None:
    """NuRO回答を登録し open→answered に遷移する。open 以外からは InvalidTransitionError。"""
    doc_ref = _collection().document(inquiry_id)

    def _save(transaction) -> None:
        current: str = _get_in_transaction(doc_ref, transaction)["status"]
        if current != "open":
            raise InvalidTransitionError(current, "answered")
        now = datetime.now(timezone.utc)
        record = InquiryAnswer(
            content=answer.content, answered_by=answer.answered_by, answered_at=now
        )
        transaction.update(
            doc_ref,
            {"answer": record.model_dump(), "status": "answered", "updated_at": now},
        )

    _run_in_transaction(_save)
    logger.info("回答を登録: inquiry_id=%s answered_by=%s", inquiry_id, answer.answered_by)


def save_draft(inquiry_id: str, draft: AskResult) -> None:
    """AIドラフト（(c)・AskResult 同型）を保存する。再生成時は上書き。"""
    doc_ref = _collection().document(inquiry_id)

    def _save(transaction) -> None:
        _get_in_transaction(doc_ref, transaction)  # 存在確認のみ（状態は問わない）
        transaction.update(
            doc_ref,
            {"ai_draft": draft.model_dump(), "updated_at": datetime.now(timezone.utc)},
        )

    _run_in_transaction(_save)


def update_status(inquiry_id: str, status: InquiryStatus) -> None:
    """電力側の状態遷移（answered→resolved／answered→open）。それ以外は InvalidTransitionError。

    open→answered は save_answer 専用（回答本文なしで answered にさせない・D-15）。
    """
    doc_ref = _collection().document(inquiry_id)

    def _update(transaction) -> None:
        current: str = _get_in_transaction(doc_ref, transaction)["status"]
        if (current, status) not in _ALLOWED_STATUS_TRANSITIONS:
            raise InvalidTransitionError(current, status)
        transaction.update(
            doc_ref, {"status": status, "updated_at": datetime.now(timezone.utc)}
        )

    _run_in_transaction(_update)
    logger.info("状態を更新: inquiry_id=%s → %s", inquiry_id, status)
