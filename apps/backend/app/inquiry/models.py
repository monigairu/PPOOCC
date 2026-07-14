"""問い合わせナレッジ対応（inquiry）のドメインモデル。

DESIGN.md §3（モジュールI/F）・§4（データ契約）の Pydantic 実装。
3段パイプライン（検索→十分性判定→引用付き生成＋接地検査）の各段の入出力と、
Firestore `inquiries` コレクションのスキーマを定義する。

本モジュールが API レスポンス（routes/inquiry.py）と Firestore 保存（store.py）の
両方から参照される契約の実体。フィールドの追加は可・意味変更/削除は DESIGN 更新とセット。
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ── 型エイリアス（契約の語彙・DESIGN §3-1／§1-3）────────────────────────────

AskStatus = Literal["answered", "abstained"]
AbstainReason = Literal["insufficient_context", "low_grounding", "gate_error"]
GateStage = Literal["sufficiency", "generation", "grounding"]  # DESIGN §6 の②③④
InquiryStatus = Literal["open", "answered", "resolved"]


# ── 引用（DESIGN §4-1・D-9）─────────────────────────────────────────────────

class Evidence(BaseModel):
    """回答の根拠1件。ユーザーが原本に当たれる粒度で提示する（REQUIREMENTS §3-1）。

    F3 は BigQuery 平坦化で1行=1メッセージ（確認/回答×回数）のため、
    引用の最小単位は record_id + round + message_direction（D-9）。
    F3由来の Evidence を組み立てる際は round / message_direction を必ず埋めること
    （重複排除・表示は `citation_key` 単位。None の場合は record_id 単位に落ちる）。

    load_f3() レコードとのフィールド対応（誤マッピング防止）:
      record_id         ← "id"（案件ID。例 "03_KT_1G_01_0002"。メッセージ単位の
                           一意キーは "_doc_id"＝message_id "{id}_{seq:02d}"）
      sheet             ← "sheet_name"
      snippet           ← "message_content" の該当箇所の抜粋（全文貼付にしない）
      score             ← "_rerank_score"
      round             ← "round"
      message_direction ← "message_direction"
    """
    record_id: str                          # F3レコードID（案件単位・例: "03_KT_1G_01_0002"）
    sheet: str                              # シート名（例: "KNI_1G_01"）
    snippet: str                            # 該当箇所の抜粋
    # BQ平坦化テーブルに原本ファイル名の列は無いため任意。pipeline が
    # utility_name から一意に導出できる場合のみ設定する（例: F3_knowledge_関東電力.xlsx）
    source_file: str | None = None
    score: float | None = None              # 検索・リランクスコア（表示は任意）
    round: int | None = None                # 何回目のやりとりか（D-9）
    # 実データの語彙は "nuro"（NuRO確認）/ "denryoku"（電力回答）
    # （excel_reader._infer_direction が生成）。日本語への変換は表示層で行う
    message_direction: str | None = None

    @property
    def citation_key(self) -> tuple[str, int | None, str | None]:
        """引用の同一性判定キー（D-9: record_id + round + message_direction）。"""
        return (self.record_id, self.round, self.message_direction)


# ── ② 十分性判定（DESIGN §3-2）──────────────────────────────────────────────

class SufficiencyResult(BaseModel):
    """検索結果で質問に回答できるかの独立LLM判定。

    部分的にしか答えられない場合は insufficient 側に倒す（DESIGN §3-2）。
    usable_record_ids は案件ID単位（メッセージ粒度の特定は Evidence が担う）。
    """
    sufficient: bool
    usable_record_ids: list[str] = Field(default_factory=list)  # 回答に使えるレコード
    reason: str = ""                                            # 判定理由（ログ・評価用）


# ── ③ 引用付き回答生成（DESIGN §3-3）────────────────────────────────────────

class GeneratedAnswer(BaseModel):
    """レコード本文のみを根拠にした回答。evidence タグ（[F3#record_id]）付き。

    cited_record_ids は answer 本文の evidence タグからパーサで導出する
    （生成側で本文とリストを別々に組み立てて二重管理しない＝食い違いの余地を残さない）。
    """
    answer: str
    cited_record_ids: list[str] = Field(default_factory=list)


# ── ④ 接地検査（DESIGN §3-4）────────────────────────────────────────────────

class ClaimCitation(BaseModel):
    """Check Grounding API が返す主張1件と、それを支持する根拠の対応。"""
    claim_text: str
    citation_indices: list[int] = Field(default_factory=list)  # 根拠facts配列へのindex


class GroundingResult(BaseModel):
    """回答が根拠レコード群に支持される度合い。score < 閾値なら棄却に切替。"""
    score: float                            # 0〜1（Check Grounding の support_score）
    claim_citations: list[ClaimCitation] = Field(default_factory=list)


# ── パイプライン入出力（DESIGN §3-1／§4-1）──────────────────────────────────

class AskRequest(BaseModel):
    """`POST /api/inquiry/ask` のリクエスト（DESIGN §4-1）。"""
    question: str = Field(min_length=1)
    utility: str = Field(min_length=1)   # 問い合わせ元電力会社名（自社フィルタに使用）


class AskResult(BaseModel):
    """`ask()` の返り値＝ `/api/inquiry/ask` のレスポンス本体。

    status="abstained" は正常系（起票に流す）。システム障害はこの型で表現せず
    例外→HTTPエラーとする（DESIGN §6：棄却とエラーを混同しない）。
    status とフィールドの整合はバリデータで強制する（矛盾状態を保存させない）。
    """
    status: AskStatus
    answer: str | None = None                               # answered 時のみ・必須
    evidences: list[Evidence] = Field(default_factory=list)  # answered 時の根拠
    grounding_score: float | None = None                    # answered 時のみ
    related: list[Evidence] = Field(default_factory=list)   # abstained 時の近傍ナレッジ
    abstain_reason: AbstainReason | None = None             # abstained 時のみ・必須
    # abstain_reason="gate_error" の時、どのゲートで落ちたか（評価・閾値較正の分析用）
    failed_stage: GateStage | None = None

    @model_validator(mode="after")
    def _check_status_consistency(self) -> "AskResult":
        if self.status == "answered":
            if self.answer is None:
                raise ValueError("status='answered' には answer が必須（DESIGN §4-1）")
            if self.abstain_reason is not None:
                raise ValueError("status='answered' に abstain_reason は設定不可（DESIGN §6）")
        else:  # abstained
            if self.answer is not None:
                raise ValueError("status='abstained' に answer は設定不可（DESIGN §4-1）")
            if self.abstain_reason is None:
                raise ValueError("status='abstained' には abstain_reason が必須（DESIGN §4-1）")
        return self


# ── 起票・回答（DESIGN §3-5／§4-2）──────────────────────────────────────────

class InquiryCreate(BaseModel):
    """起票リクエスト（POST /api/inquiry）。number・status はサーバ側で採番/設定。"""
    category: str = Field(min_length=1)   # PoCは自由入力（例: "質問"・§4-2）
    content: str = Field(min_length=1)
    requester: str = Field(min_length=1)
    self_solve_log: AskResult | None = None  # 起票直前のRAG応答（評価・将来(d)の入力）


class InquiryCreated(BaseModel):
    """起票レスポンス（POST /api/inquiry → 201・DESIGN §4-1）。"""
    inquiry_id: str
    number: str


class StatusUpdate(BaseModel):
    """状態更新リクエスト（PATCH /api/inquiry/{id}/status・DESIGN §4-1／D-15）。

    電力側遷移（answered→resolved／answered→open）専用のため "answered" は受けない
    （open→answered は /answer のみ）。遷移可否の検証本体は store.update_status。
    """
    status: Literal["resolved", "open"]


class AnswerCreate(BaseModel):
    """NuRO回答の登録リクエスト（POST /api/inquiry/{id}/answer）。"""
    content: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)


class InquiryAnswer(BaseModel):
    """保存済みのNuRO回答。answered_at はサーバ側で設定する。"""
    content: str
    answered_by: str
    answered_at: datetime


class Inquiry(BaseModel):
    """Firestore `inquiries/{inquiry_id}` の1ドキュメント（DESIGN §4-2）。

    タイムスタンプは既存の事前レビュー慣行に合わせ、store.py 側で
    tz-aware な `datetime.now(timezone.utc)` を書き込む（SERVER_TIMESTAMP は
    使わない。Firestore 読出の DatetimeWithNanoseconds は datetime のサブクラス
    のためそのまま検証を通る）。
    """
    inquiry_id: str
    number: str                              # 自動採番（例: "0001"）
    category: str
    content: str
    requester: str
    status: InquiryStatus = "open"
    created_at: datetime
    updated_at: datetime
    self_solve_log: AskResult | None = None
    ai_draft: AskResult | None = None        # (c) AIドラフト（再生成で上書き）
    answer: InquiryAnswer | None = None
