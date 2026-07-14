/**
 * /inquiry/tickets/:id — 問い合わせ詳細（フェーズ2・DESIGN §2）
 *
 * ロール別の操作（§1-1・§1-3・D-15/D-16）：
 *   - NuRO担当者：status=open のとき回答を登録（open→answered）。
 *     起票時の自己解決記録（self_solve_log）を回答の参考として表示。
 *   - 電力ユーザー：status=answered のとき「解決した」（→resolved）または
 *     「解決しない（差し戻し）」（→open）。
 *   - AIドラフト（ai_draft・フェーズ3）：NuROが「生成」ボタンで ask() を再実行し、
 *     ドラフト＋根拠（棄却時は近傍ナレッジ）を回答の参考として表示（自動送信はしない・§3-3）。
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { generateDraft, getInquiry, submitAnswer, updateStatus } from "./api.js";
import {
  ABSTAIN_INFO, C, ErrorCard, EvidenceCard, InquiryShell, Spinner, StatusBadge,
  formatTimestamp, useIdentity,
} from "./shared.jsx";

function SectionCard({ title, accent = C.border, children }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${accent}`, borderRadius: "10px", overflow: "hidden" }}>
      <div style={{ padding: "8px 16px", borderBottom: `1px solid ${C.border}`, fontSize: "11px", fontWeight: 700, color: C.textMuted }}>
        {title}
      </div>
      <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: "12px" }}>
        {children}
      </div>
    </div>
  );
}

// 起票時の自己解決記録（NuROが回答を書く際の参考・§4-2 self_solve_log）
function SelfSolveLog({ log }) {
  if (!log) return null;
  const abstainInfo = log.status === "abstained" ? (ABSTAIN_INFO[log.abstain_reason] || null) : null;
  const relatedEvidences = log.status === "abstained" ? log.related : log.evidences;
  return (
    <SectionCard title="起票時のAI検索記録（self_solve_log）— 回答の参考">
      <div style={{ fontSize: "12px", color: C.textMuted, lineHeight: 1.8 }}>
        {log.status === "abstained"
          ? <>AIは回答を差し控えました{abstainInfo && <>：{abstainInfo.title}</>}</>
          : <>AIは回答を提示しましたが、起票者は自己解決できませんでした。</>}
      </div>
      {log.status === "answered" && log.answer && (
        <div style={{
          padding: "10px 14px", borderRadius: "6px", background: "rgba(255,255,255,0.02)",
          border: `1px solid ${C.border}`, fontSize: "12.5px", color: C.text,
          lineHeight: 1.9, whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          {log.answer}
        </div>
      )}
      {relatedEvidences?.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted }}>
            {log.status === "abstained" ? "検索でヒットした近傍ナレッジ" : "回答の根拠レコード"}
          </div>
          {relatedEvidences.map((ev, i) => <EvidenceCard key={i} ev={ev} />)}
        </div>
      )}
    </SectionCard>
  );
}

// AIドラフト（(c)・フェーズ3）：NuROの回答参考。生成はオンデマンド・再生成で上書き（D-17）
function AiDraftSection({ inquiryId, draft, onGenerated }) {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState(null);

  const handleGenerate = async () => {
    if (generating) return;
    setGenerating(true);
    setError(null);
    try {
      await generateDraft(inquiryId);
      onGenerated(); // 詳細を再読込して保存済み ai_draft を表示
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  };

  const abstainInfo =
    draft?.status === "abstained" ? ABSTAIN_INFO[draft.abstain_reason] || ABSTAIN_INFO.insufficient_context : null;
  const draftEvidences = draft?.status === "abstained" ? draft.related : draft?.evidences;

  return (
    <SectionCard title="AIドラフト — 回答の参考（自動送信はされません）" accent={C.accent}>
      {!draft && (
        <div style={{ fontSize: "12px", color: C.textMuted, lineHeight: 1.8 }}>
          問い合わせ内容でナレッジを再検索し、回答ドラフトまたは関連する近傍ナレッジを生成できます。
        </div>
      )}

      {draft?.status === "answered" && (
        <>
          <div style={{
            padding: "10px 14px", borderRadius: "6px", background: "rgba(255,255,255,0.02)",
            border: `1px solid ${C.border}`, fontSize: "12.5px", color: C.text,
            lineHeight: 1.9, whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {draft.answer}
          </div>
          {draft.grounding_score != null && (
            <div style={{ fontSize: "11px", color: C.textMuted }}>
              接地スコア：{draft.grounding_score.toFixed(2)}（根拠レコードに支持される度合い）
            </div>
          )}
        </>
      )}

      {abstainInfo && (
        <div style={{ fontSize: "12px", color: C.textMuted, lineHeight: 1.8 }}>
          ⚠ {abstainInfo.title} — ナレッジからドラフトを作成できませんでした。
          下記の近傍ナレッジを参考に回答を作成してください。
        </div>
      )}

      {draftEvidences?.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted }}>
            {draft.status === "abstained"
              ? "検索でヒットした近傍ナレッジ（回答ではありません）"
              : "ドラフトの根拠レコード"}
          </div>
          {draftEvidences.map((ev, i) => <EvidenceCard key={i} ev={ev} />)}
        </div>
      )}

      {error && <ErrorCard message={error} />}
      <button
        onClick={handleGenerate}
        disabled={generating}
        style={{
          alignSelf: "flex-start", padding: "8px 20px", borderRadius: "6px",
          border: `1px solid ${C.accent}`, background: "transparent",
          color: generating ? C.textDim : C.accent,
          fontSize: "12px", fontWeight: 700, fontFamily: "inherit",
          cursor: generating ? "not-allowed" : "pointer",
          display: "flex", alignItems: "center", gap: "8px",
        }}
      >
        {generating
          ? <><Spinner size={12} />生成中...（ナレッジを検索しています）</>
          : draft ? "↻ ドラフトを再生成する" : "✨ AIドラフトを生成する"}
      </button>
    </SectionCard>
  );
}

// NuRO回答フォーム（open のみ・open→answered）
function AnswerForm({ inquiryId, answeredBy, onDone }) {
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const canSubmit = !submitting && content.trim() && answeredBy.trim();

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitAnswer(inquiryId, { content: content.trim(), answeredBy: answeredBy.trim() });
      onDone();
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  };

  return (
    <SectionCard title="回答を登録（NuRO担当者）" accent={C.accent}>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={5}
        placeholder="回答を入力してください（登録すると起票者に表示されます）"
        style={{
          width: "100%", resize: "vertical", padding: "10px 12px", borderRadius: "8px",
          border: `1px solid ${C.borderLight}`, background: C.bg,
          color: C.text, fontSize: "13px", lineHeight: 1.8, outline: "none", fontFamily: "inherit",
        }}
      />
      {error && <ErrorCard message={error} />}
      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <span style={{ fontSize: "11px", color: C.textMuted }}>回答者：{answeredBy || "（右上の表示名を入力してください）"}</span>
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          style={{
            marginLeft: "auto", padding: "8px 20px", borderRadius: "6px", border: "none",
            background: canSubmit ? `linear-gradient(135deg, ${C.accent}, #6366f1)` : C.borderLight,
            color: canSubmit ? "#fff" : C.textDim,
            fontSize: "12px", fontWeight: 700, fontFamily: "inherit",
            cursor: canSubmit ? "pointer" : "not-allowed",
            display: "flex", alignItems: "center", gap: "8px",
          }}
        >
          {submitting ? <><Spinner size={12} />登録中...</> : "回答を登録する"}
        </button>
      </div>
    </SectionCard>
  );
}

// 電力側の解決確認（answered のみ・answered→resolved / answered→open）
function ResolveActions({ inquiryId, onDone }) {
  const [submitting, setSubmitting] = useState(null); // "resolved" | "open" | null
  const [error, setError] = useState(null);

  const transition = async (status) => {
    if (submitting) return;
    setSubmitting(status);
    setError(null);
    try {
      await updateStatus(inquiryId, status);
      onDone();
    } catch (e) {
      setError(e.message);
      setSubmitting(null);
    }
  };

  const buttonStyle = (color, active) => ({
    padding: "8px 20px", borderRadius: "6px",
    border: `1px solid ${color}`, background: "transparent",
    color, fontSize: "12px", fontWeight: 700, fontFamily: "inherit",
    cursor: submitting ? "not-allowed" : "pointer", opacity: submitting && !active ? 0.5 : 1,
    display: "flex", alignItems: "center", gap: "8px",
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {error && <ErrorCard message={error} />}
      <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
        <button onClick={() => transition("resolved")} disabled={!!submitting} style={buttonStyle(C.success, submitting === "resolved")}>
          {submitting === "resolved" ? <><Spinner size={12} />更新中...</> : "✓ 解決した"}
        </button>
        <button onClick={() => transition("open")} disabled={!!submitting} style={buttonStyle(C.warning, submitting === "open")}>
          {submitting === "open" ? <><Spinner size={12} />更新中...</> : "↩ 解決しない（未回答に差し戻す）"}
        </button>
      </div>
      <span style={{ fontSize: "10.5px", color: C.textDim }}>
        差し戻すと未回答に戻り、NuROが再度回答します（必要なら質問画面から追記して再起票もできます）
      </span>
    </div>
  );
}

export default function InquiryDetailPage() {
  const { inquiryId } = useParams();
  const navigate = useNavigate();
  const [identity, updateIdentity] = useIdentity();
  const [inquiry, setInquiry] = useState(null);
  const [error, setError] = useState(null);

  const reload = useCallback(() => {
    setError(null);
    getInquiry(inquiryId)
      .then(setInquiry)
      .catch((e) => setError(e.message));
  }, [inquiryId]);

  useEffect(() => { reload(); }, [reload]);

  const isNuro = identity.role === "nuro";

  return (
    <InquiryShell
      rightSlot="問い合わせ詳細"
      identity={identity}
      onIdentityChange={updateIdentity}
      maxWidth="860px"
    >
      <button
        onClick={() => navigate("/inquiry/tickets")}
        style={{
          alignSelf: "flex-start", padding: "4px 0", border: "none", background: "transparent",
          color: C.textMuted, fontSize: "12px", fontFamily: "inherit", cursor: "pointer",
        }}
      >
        ← 一覧に戻る
      </button>

      {error && <ErrorCard message={error} />}
      {!error && !inquiry && (
        <div style={{ display: "flex", alignItems: "center", gap: "10px", color: C.textMuted, fontSize: "12px" }}>
          <Spinner />読み込み中...
        </div>
      )}

      {inquiry && (
        <>
          {/* ヘッダー情報 */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <span style={{ fontFamily: "monospace", fontSize: "15px", fontWeight: 700, color: C.accent }}>
              No.{inquiry.number}
            </span>
            <StatusBadge status={inquiry.status} />
            <span style={{
              padding: "1px 8px", borderRadius: "4px", fontSize: "10px",
              color: C.textMuted, border: `1px solid ${C.border}`,
            }}>
              {inquiry.category}
            </span>
            <span style={{ fontSize: "11px", color: C.textMuted }}>起票者：{inquiry.requester}</span>
            <span style={{ marginLeft: "auto", fontSize: "10.5px", color: C.textDim, fontFamily: "monospace" }}>
              起票 {formatTimestamp(inquiry.created_at)} ／ 更新 {formatTimestamp(inquiry.updated_at)}
            </span>
          </div>

          {/* 問い合わせ内容 */}
          <SectionCard title="問い合わせ内容">
            <div style={{ fontSize: "13.5px", color: C.text, lineHeight: 1.95, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {inquiry.content}
            </div>
          </SectionCard>

          {/* NuRO回答（登録済み） */}
          {inquiry.answer && (
            <SectionCard title="NuROからの回答" accent={C.success}>
              <div style={{ fontSize: "13.5px", color: C.text, lineHeight: 1.95, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {inquiry.answer.content}
              </div>
              <div style={{ fontSize: "11px", color: C.textMuted, borderTop: `1px solid ${C.border}`, paddingTop: "8px" }}>
                回答者：{inquiry.answer.answered_by}（{formatTimestamp(inquiry.answer.answered_at)}）
              </div>
            </SectionCard>
          )}

          {/* ロール別の操作 */}
          {isNuro && inquiry.status === "open" && (
            <AnswerForm inquiryId={inquiry.inquiry_id} answeredBy={identity.displayName} onDone={reload} />
          )}
          {!isNuro && inquiry.status === "answered" && (
            <ResolveActions inquiryId={inquiry.inquiry_id} onDone={reload} />
          )}
          {isNuro && inquiry.status === "answered" && (
            <span style={{ fontSize: "11px", color: C.textDim }}>
              電力ユーザーの解決確認待ちです（差し戻された場合は再度回答できます）
            </span>
          )}
          {inquiry.status === "resolved" && (
            <span style={{ fontSize: "11px", color: C.success }}>✓ この問い合わせは解決済みです</span>
          )}

          {/* NuROの回答参考：AIドラフト（オンデマンド生成）＋起票時のAI検索記録 */}
          {isNuro && (
            <AiDraftSection
              inquiryId={inquiry.inquiry_id}
              draft={inquiry.ai_draft}
              onGenerated={reload}
            />
          )}
          {isNuro && <SelfSolveLog log={inquiry.self_solve_log} />}
        </>
      )}
    </InquiryShell>
  );
}
