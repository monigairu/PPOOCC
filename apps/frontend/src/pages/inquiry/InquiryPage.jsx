/**
 * /inquiry — 電力ユーザー向け 問い合わせナレッジ検索画面（フェーズ1＋フェーズ2起票導線）
 *
 * 機能UX：質問→引用付き回答（引用チップ⇄根拠カードのホバー連動）／
 * 処理ステップ表示（推定）／接地スコア／棄却・未解決→起票フォーム（質問文プリフィル）。
 *
 * 挙動仕様（DESIGN §1-1・§4-1・§6）：
 *   - POST /api/inquiry/ask。棄却（abstained）は正常系＝起票フォームを開いた状態で表示。
 *   - 回答（answered）でも自己解決しない場合は起票できる（§1-1 の SELF=いいえ 分岐）。
 *   - 起票時は直前の AskResult を self_solve_log として添付（§4-2・評価と将来(d)の入力）。
 *   - gate_error は「検証未完了のため起票を推奨」。502等の障害はエラー表示（起票を誘発させない）。
 */
import { useMemo, useRef, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { askQuestion, createInquiry } from "./api.js";
import {
  ABSTAIN_INFO, C, ErrorCard, EvidenceCard, InquiryShell, Spinner, useIdentity,
} from "./shared.jsx";

// サンプル質問（qa_cases.yaml A群より）
const SAMPLE_QUESTIONS = [
  "実施費用低減策はどこまで具体的に書く必要がありますか？",
  "人件費の単価が他社より高い場合、どんな積算根拠が必要ですか？",
  "仮設備費を「1式」で計上する場合の内訳明細は？",
  "実績が計画を15%超過した場合の説明資料は？",
];

// 処理ステップ（DESIGN §1-2）。sec は経過秒による推定表示のしきい値
const PIPELINE_STEPS = [
  { label: "ナレッジ検索", sec: 0 },
  { label: "十分性判定", sec: 5 },
  { label: "回答生成", sec: 14 },
  { label: "接地検査", sec: 26 },
];

let entrySeq = 0;

// ── 処理中カード（4ステップの推定進行） ─────────────────────────────────────
function LoadingCard({ startedAt }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setElapsed((Date.now() - startedAt) / 1000), 500);
    return () => clearInterval(t);
  }, [startedAt]);

  const activeIdx = PIPELINE_STEPS.reduce((acc, s, i) => (elapsed >= s.sec ? i : acc), 0);

  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: "10px",
      padding: "14px 18px", display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap",
    }}>
      <Spinner />
      <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
        {PIPELINE_STEPS.map((s, i) => {
          const state = i < activeIdx ? "done" : i === activeIdx ? "active" : "wait";
          return (
            <span key={s.label} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{
                padding: "3px 10px", borderRadius: "4px", fontSize: "11px", fontWeight: 600,
                color: state === "active" ? C.accent : state === "done" ? C.textMuted : C.textDim,
                background: state === "active" ? C.accentSoft : "transparent",
                border: `1px solid ${state === "active" ? C.accent : C.border}`,
                transition: "all 0.3s",
              }}>
                {state === "done" ? "✓ " : ""}{s.label}
              </span>
              {i < PIPELINE_STEPS.length - 1 && <span style={{ color: C.textDim, fontSize: "10px" }}>›</span>}
            </span>
          );
        })}
      </div>
      <span style={{ fontSize: "10px", color: C.textDim, marginLeft: "auto" }}>
        {elapsed.toFixed(0)}秒経過（段階は推定表示）
      </span>
    </div>
  );
}

// ── 回答本文（引用タグ → 番号チップ・根拠カード連動） ───────────────────────
function AnswerBody({ text, citeNumOf, hoveredId, setHoveredId, onCiteClick }) {
  const parts = text.split(/(\[F3#[0-9A-Za-z_-]+\])/g);
  return (
    <div style={{ fontSize: "13.5px", lineHeight: 1.95, color: C.text, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {parts.map((part, i) => {
        const m = part.match(/^\[F3#([0-9A-Za-z_-]+)\]$/);
        if (!m) return <span key={i}>{part}</span>;
        const id = m[1];
        const num = citeNumOf(id);
        const active = hoveredId === id;
        return (
          <button
            key={i}
            onMouseEnter={() => setHoveredId(id)}
            onMouseLeave={() => setHoveredId(null)}
            onClick={() => onCiteClick(id)}
            title={`根拠 ${id} を表示`}
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              minWidth: "18px", height: "16px", padding: "0 5px", margin: "0 3px", verticalAlign: "2px",
              fontSize: "10px", fontWeight: 700, fontFamily: "monospace",
              color: active ? "#fff" : C.accent,
              background: active ? C.accent : C.accentSoft,
              border: `1px solid ${C.accent}`, borderRadius: "3px",
              cursor: "pointer", transition: "all 0.15s",
            }}
          >
            {num != null ? num : "F3"}
          </button>
        );
      })}
    </div>
  );
}

// ── 起票フォーム（質問文プリフィル・self_solve_log 添付。§1-1 FILE） ────────
function FilingSection({ question, selfSolveLog, requester, defaultOpen }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(defaultOpen);
  const [category, setCategory] = useState("質問");
  const [content, setContent] = useState(question);
  const [submitting, setSubmitting] = useState(false);
  const [filed, setFiled] = useState(null); // { inquiry_id, number }
  const [error, setError] = useState(null);

  const handleSubmit = async () => {
    if (submitting || !content.trim() || !requester.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      setFiled(await createInquiry({
        category: category.trim() || "質問",
        content: content.trim(),
        requester: requester.trim(),
        selfSolveLog,
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (filed) {
    return (
      <div style={{
        padding: "12px 14px", borderRadius: "8px",
        background: C.successSoft, border: `1px solid ${C.success}`,
        display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap",
      }}>
        <span style={{ fontSize: "12.5px", fontWeight: 700, color: C.success }}>
          ✓ 起票しました（No.{filed.number}）。NuROからの回答をお待ちください。
        </span>
        <button
          onClick={() => navigate(`/inquiry/tickets/${filed.inquiry_id}`)}
          style={{
            marginLeft: "auto", padding: "6px 14px", borderRadius: "6px",
            border: `1px solid ${C.success}`, background: "transparent",
            color: C.success, fontSize: "11.5px", fontWeight: 700,
            fontFamily: "inherit", cursor: "pointer",
          }}
        >
          詳細を開く →
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          alignSelf: "flex-start", padding: "8px 18px", borderRadius: "6px",
          border: `1px solid ${C.borderLight}`, background: "transparent",
          color: C.textMuted, fontSize: "12px", fontWeight: 700, fontFamily: "inherit",
          cursor: "pointer",
        }}
      >
        ✉ 解決しない場合はこの内容で起票する
      </button>
    );
  }

  const inputStyle = {
    padding: "8px 12px", borderRadius: "6px",
    border: `1px solid ${C.borderLight}`, background: C.bg,
    color: C.text, fontSize: "12.5px", outline: "none", fontFamily: "inherit",
  };
  const canSubmit = !submitting && content.trim() && requester.trim();

  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: "10px",
      padding: "14px", borderRadius: "8px",
      background: "rgba(255,255,255,0.02)", border: `1px dashed ${C.borderLight}`,
    }}>
      <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted }}>
        問い合わせを起票（内容は編集できます。検索結果の記録も一緒に送信されます）
      </div>
      <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: "11px", color: C.textMuted, fontWeight: 600 }}>分類</span>
        <input value={category} onChange={(e) => setCategory(e.target.value)} style={{ ...inputStyle, width: "120px" }} />
        <span style={{ fontSize: "11px", color: C.textMuted, fontWeight: 600 }}>起票者</span>
        <span style={{ fontSize: "12px", color: C.text }}>{requester || "（右上の表示名を入力してください）"}</span>
      </div>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={4}
        style={{ ...inputStyle, width: "100%", resize: "vertical", lineHeight: 1.8 }}
      />
      {error && <ErrorCard message={error} />}
      <button
        onClick={handleSubmit}
        disabled={!canSubmit}
        style={{
          alignSelf: "flex-start", padding: "8px 20px", borderRadius: "6px", border: "none",
          background: canSubmit ? `linear-gradient(135deg, ${C.accent}, #6366f1)` : C.borderLight,
          color: canSubmit ? "#fff" : C.textDim,
          fontSize: "12px", fontWeight: 700, fontFamily: "inherit",
          cursor: canSubmit ? "pointer" : "not-allowed",
          display: "flex", alignItems: "center", gap: "8px",
        }}
      >
        {submitting ? <><Spinner size={12} />起票中...</> : "✉ この内容で起票する"}
      </button>
    </div>
  );
}

// ── 回答カード（answered） ───────────────────────────────────────────────────
function AnsweredCard({ result, question, requester }) {
  const [hoveredId, setHoveredId] = useState(null);
  const cardRefs = useRef({});

  // 引用番号：evidences の record_id 出現順に採番（同一案件の複数メッセージは同番号）
  const citeNums = useMemo(() => {
    const map = new Map();
    result.evidences.forEach((ev) => {
      if (!map.has(ev.record_id)) map.set(ev.record_id, map.size + 1);
    });
    return map;
  }, [result.evidences]);

  const scrollToEvidence = (id) =>
    cardRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "center" });

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.success}`, borderRadius: "10px", overflow: "hidden" }}>
      <div style={{
        padding: "10px 18px", background: C.successSoft, borderBottom: `1px solid ${C.border}`,
        display: "flex", alignItems: "center", gap: "10px",
      }}>
        <span style={{ fontSize: "12px", fontWeight: 700, color: C.success }}>✓ ナレッジから回答が見つかりました</span>
        {result.grounding_score != null && (
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontSize: "10px", color: C.textDim }}>接地スコア</span>
            <span style={{ width: "70px", height: "5px", borderRadius: "3px", background: C.border, overflow: "hidden" }}>
              <span style={{
                display: "block", height: "100%", width: `${Math.min(1, result.grounding_score) * 100}%`,
                background: C.success, borderRadius: "3px",
              }} />
            </span>
            <span style={{ fontSize: "11px", fontWeight: 700, fontFamily: "monospace", color: C.success }}>
              {result.grounding_score.toFixed(2)}
            </span>
          </span>
        )}
      </div>

      <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: "14px" }}>
        <AnswerBody
          text={result.answer}
          citeNumOf={(id) => citeNums.get(id)}
          hoveredId={hoveredId}
          setHoveredId={setHoveredId}
          onCiteClick={scrollToEvidence}
        />

        {result.evidences.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted }}>
              根拠レコード（{result.evidences.length}件・番号は本文の引用と対応）
            </div>
            {result.evidences.map((ev, i) => (
              <EvidenceCard
                key={i}
                ev={ev}
                num={citeNums.get(ev.record_id)}
                highlighted={hoveredId === ev.record_id}
                onHover={setHoveredId}
                cardRef={(el) => { if (el) cardRefs.current[ev.record_id] = el; }}
              />
            ))}
          </div>
        )}

        {/* 自己解決しない場合の起票導線（§1-1 SELF=いいえ。折りたたみで提示） */}
        <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: "12px", display: "flex", flexDirection: "column" }}>
          <FilingSection question={question} selfSolveLog={result} requester={requester} defaultOpen={false} />
        </div>
      </div>
    </div>
  );
}

// ── 棄却カード（abstained・正常系） ──────────────────────────────────────────
function AbstainedCard({ result, question, requester }) {
  const info = ABSTAIN_INFO[result.abstain_reason] || ABSTAIN_INFO.insufficient_context;
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.warning}`, borderRadius: "10px", overflow: "hidden" }}>
      <div style={{ padding: "10px 18px", background: C.warningSoft, borderBottom: `1px solid ${C.border}` }}>
        <span style={{ fontSize: "12px", fontWeight: 700, color: C.warning }}>⚠ {info.title}</span>
      </div>
      <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: "14px" }}>
        <div style={{ fontSize: "12.5px", color: C.textMuted, lineHeight: 1.85 }}>{info.desc}</div>

        {/* 棄却時は起票フォームを開いた状態で提示（§1-1 ABS→FILE） */}
        <FilingSection question={question} selfSolveLog={result} requester={requester} defaultOpen />

        {result.related.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted }}>
              参考：関連するかもしれないナレッジ（回答ではありません）
            </div>
            {result.related.map((ev, i) => <EvidenceCard key={i} ev={ev} />)}
          </div>
        )}
      </div>
    </div>
  );
}

// ── メイン ───────────────────────────────────────────────────────────────────
export default function InquiryPage() {
  const [identity, updateIdentity] = useIdentity();
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState([]); // {id, question, status: loading|done|error, result?, error?, startedAt}

  const isLoading = thread.some((e) => e.status === "loading");

  const handleAsk = async (text) => {
    const q = (text ?? question).trim();
    const u = identity.utility.trim();
    if (!q || !u || isLoading) return;

    const id = ++entrySeq;
    setQuestion("");
    // 新しい質問を先頭に（入力欄の直下に最新の結果が来る）
    setThread((prev) => [{ id, question: q, status: "loading", startedAt: Date.now() }, ...prev]);

    const settle = (patch) =>
      setThread((prev) => prev.map((e) => (e.id === id ? { ...e, ...patch } : e)));

    try {
      settle({ status: "done", result: await askQuestion(q, u) });
    } catch (e) {
      settle({ status: "error", error: e.message });
    }
  };

  const isAskDisabled = !question.trim() || !identity.utility.trim() || isLoading;

  return (
    <InquiryShell
      rightSlot="問い合わせ — F3自社ナレッジから引用付きで回答"
      identity={identity}
      onIdentityChange={updateIdentity}
    >
      {/* ── 質問入力 ── */}
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`, borderRadius: "10px",
        padding: "16px 18px", display: "flex", flexDirection: "column", gap: "10px",
      }}>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              handleAsk();
            }
          }}
          rows={3}
          placeholder="廃炉業務に関する質問を入力してください（Enterで送信 / Shift+Enterで改行）"
          style={{
            width: "100%", resize: "vertical",
            padding: "10px 12px", borderRadius: "8px",
            border: `1px solid ${C.borderLight}`, background: C.bg,
            color: C.text, fontSize: "13px", lineHeight: 1.8,
            outline: "none", fontFamily: "inherit",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontSize: "10px", color: C.textDim }}>
            検索対象は自社（{identity.utility || "未設定"}）のナレッジのみです（会社名は右上で変更）
          </span>
          <button
            onClick={() => handleAsk()}
            disabled={isAskDisabled}
            style={{
              marginLeft: "auto", padding: "8px 22px", borderRadius: "8px", border: "none",
              background: isAskDisabled ? C.borderLight : `linear-gradient(135deg, ${C.accent}, #6366f1)`,
              color: isAskDisabled ? C.textDim : "#fff",
              fontSize: "12.5px", fontWeight: 700, fontFamily: "inherit",
              cursor: isAskDisabled ? "not-allowed" : "pointer",
              display: "flex", alignItems: "center", gap: "8px",
            }}
          >
            {isLoading ? <><Spinner size={12} />検証中...</> : "▶ 質問する"}
          </button>
        </div>
      </div>

      {/* ── サンプル質問（初回のみ） ── */}
      {thread.length === 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted }}>質問の例（クリックで入力）</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {SAMPLE_QUESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => setQuestion(s)}
                style={{
                  padding: "6px 12px", borderRadius: "6px", fontSize: "11.5px",
                  color: C.textMuted, background: "transparent",
                  border: `1px solid ${C.border}`, cursor: "pointer",
                  fontFamily: "inherit", transition: "all 0.15s", textAlign: "left",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = C.accent;
                  e.currentTarget.style.color = C.accent;
                  e.currentTarget.style.background = C.accentSoft;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = C.border;
                  e.currentTarget.style.color = C.textMuted;
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── 結果スレッド（新しい順） ── */}
      {thread.map((entry) => (
        <div key={entry.id} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <div style={{ display: "flex", gap: "10px", alignItems: "flex-start" }}>
            <span style={{
              flexShrink: 0, marginTop: "2px", padding: "2px 8px", borderRadius: "4px",
              fontSize: "10px", fontWeight: 700, color: C.accent,
              background: C.accentSoft, border: `1px solid ${C.accent}`,
            }}>
              Q
            </span>
            <div style={{ fontSize: "13.5px", fontWeight: 600, color: C.text, lineHeight: 1.7 }}>
              {entry.question}
            </div>
          </div>
          {entry.status === "loading" && <LoadingCard startedAt={entry.startedAt} />}
          {entry.status === "error" && <ErrorCard message={entry.error} />}
          {entry.status === "done" && entry.result.status === "answered" && (
            <AnsweredCard result={entry.result} question={entry.question} requester={identity.displayName} />
          )}
          {entry.status === "done" && entry.result.status === "abstained" && (
            <AbstainedCard result={entry.result} question={entry.question} requester={identity.displayName} />
          )}
        </div>
      ))}
    </InquiryShell>
  );
}
