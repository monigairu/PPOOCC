/**
 * /inquiry — 電力ユーザー向け 問い合わせナレッジ検索画面（フェーズ1）
 *
 * デザインは既存ページ（App.jsx / ReviewPage.jsx）と同一言語：
 * ダーク背景・ブルーアクセント・Noto Sans JP・角丸カード。
 * 機能UX：質問→引用付き回答（引用チップ⇄根拠カードのホバー連動）／
 * 処理ステップ表示（推定）／接地スコア／棄却→起票プリフィルのプレビュー。
 *
 * 挙動仕様（DESIGN §4-1/§6）：
 *   - POST /api/inquiry/ask。棄却（abstained）は正常系＝起票導線（フェーズ2で有効化）。
 *   - gate_error は「検証未完了のため起票を推奨」。502等の障害はエラー表示（起票を誘発させない）。
 */
import { useMemo, useRef, useState, useEffect } from "react";
import { AppHeader } from "../App.jsx";

const API_BASE = "http://localhost:8000/api";

// App.jsx と同一のカラートークン
const C = {
  bg: "#0f1117",
  surface: "#1a1d27",
  surfaceHover: "#22263a",
  border: "#2a2e42",
  borderLight: "#363b55",
  accent: "#4f8ef7",
  accentSoft: "rgba(79,142,247,0.12)",
  success: "#34d399",
  successSoft: "rgba(52,211,153,0.1)",
  warning: "#fbbf24",
  warningSoft: "rgba(251,191,36,0.12)",
  red: "#f87171",
  redSoft: "rgba(248,113,113,0.12)",
  text: "#e2e8f0",
  textMuted: "#8892a4",
  textDim: "#4a5568",
};

const DIRECTION_LABELS = { nuro: "NuRO確認", denryoku: "電力回答" };

// 棄却理由 → 表示（DESIGN §6）
const ABSTAIN_INFO = {
  insufficient_context: {
    title: "ナレッジに該当する情報が見つかりませんでした",
    desc: "過去の問合せ履歴には、この質問に直接答えられる記録がありません。問い合わせを起票するとNuROから回答を得られます。",
  },
  low_grounding: {
    title: "確実な回答を作成できませんでした",
    desc: "回答候補が根拠レコードに十分支持されなかったため、誤答を避けて回答を差し控えました。起票してのお問い合わせを推奨します。",
  },
  gate_error: {
    title: "回答の検証が完了しませんでした",
    desc: "検証が完了しなかったため回答を表示できません。起票してのお問い合わせを推奨します。",
  },
};

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

function Spinner({ size = 14 }) {
  return (
    <span style={{
      width: size, height: size, display: "inline-block", flexShrink: 0,
      border: "2px solid rgba(255,255,255,0.25)", borderTop: `2px solid ${C.accent}`,
      borderRadius: "50%", animation: "spin 0.8s linear infinite",
    }} />
  );
}

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

// ── 根拠・関連ナレッジカード ─────────────────────────────────────────────────
function EvidenceCard({ ev, num, highlighted, onHover, cardRef }) {
  const isNuro = ev.message_direction === "nuro";
  const dirLabel = DIRECTION_LABELS[ev.message_direction] || ev.message_direction || "";
  return (
    <div
      ref={cardRef}
      onMouseEnter={() => onHover?.(ev.record_id)}
      onMouseLeave={() => onHover?.(null)}
      style={{
        padding: "10px 14px", borderRadius: "8px",
        background: highlighted ? C.accentSoft : "rgba(255,255,255,0.02)",
        border: `1px solid ${highlighted ? C.accent : C.border}`,
        transition: "all 0.15s", display: "flex", gap: "12px", alignItems: "flex-start",
      }}
    >
      {num != null && (
        <span style={{
          flexShrink: 0, minWidth: "18px", height: "18px", marginTop: "1px",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: "10px", fontWeight: 700, fontFamily: "monospace",
          color: highlighted ? "#fff" : C.accent,
          background: highlighted ? C.accent : C.accentSoft,
          border: `1px solid ${C.accent}`, borderRadius: "3px",
        }}>
          {num}
        </span>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", marginBottom: "5px" }}>
          <span style={{
            padding: "1px 8px", borderRadius: "4px", fontSize: "11px", fontWeight: 700, fontFamily: "monospace",
            color: C.accent, background: C.accentSoft, border: `1px solid ${C.accent}`,
          }}>
            {ev.record_id}
          </span>
          {ev.sheet && <span style={{ fontSize: "11px", color: C.textMuted }}>{ev.sheet}</span>}
          {ev.round != null && <span style={{ fontSize: "11px", color: C.textMuted }}>{ev.round}回目</span>}
          {dirLabel && (
            <span style={{
              padding: "1px 7px", borderRadius: "3px", fontSize: "10px", fontWeight: 700,
              color: isNuro ? C.warning : C.success,
              background: isNuro ? C.warningSoft : C.successSoft,
              border: `1px solid ${isNuro ? C.warning : C.success}`,
            }}>
              {dirLabel}
            </span>
          )}
          {ev.source_file && (
            <span style={{ fontSize: "10px", color: C.textDim, marginLeft: "auto" }}>📄 {ev.source_file}</span>
          )}
        </div>
        <div style={{ fontSize: "12px", color: C.textMuted, lineHeight: 1.75 }}>{ev.snippet}</div>
      </div>
    </div>
  );
}

// ── 回答カード（answered） ───────────────────────────────────────────────────
function AnsweredCard({ result }) {
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

        <div style={{ fontSize: "11px", color: C.textDim, borderTop: `1px solid ${C.border}`, paddingTop: "10px" }}>
          解決しない場合は同じ内容で問い合わせを起票できます（起票フォームはフェーズ2で提供予定）
        </div>
      </div>
    </div>
  );
}

// ── 棄却カード（abstained・正常系） ──────────────────────────────────────────
function AbstainedCard({ result, question }) {
  const info = ABSTAIN_INFO[result.abstain_reason] || ABSTAIN_INFO.insufficient_context;
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.warning}`, borderRadius: "10px", overflow: "hidden" }}>
      <div style={{ padding: "10px 18px", background: C.warningSoft, borderBottom: `1px solid ${C.border}` }}>
        <span style={{ fontSize: "12px", fontWeight: 700, color: C.warning }}>⚠ {info.title}</span>
      </div>
      <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: "14px" }}>
        <div style={{ fontSize: "12.5px", color: C.textMuted, lineHeight: 1.85 }}>{info.desc}</div>

        <div>
          <div style={{ fontSize: "11px", fontWeight: 700, color: C.textMuted, marginBottom: "6px" }}>
            起票内容プレビュー（質問文をそのまま引き継ぎます）
          </div>
          <div style={{
            padding: "10px 14px", borderRadius: "6px",
            background: "rgba(255,255,255,0.02)", border: `1px dashed ${C.borderLight}`,
            fontSize: "12.5px", color: C.text, lineHeight: 1.8,
          }}>
            {question}
          </div>
        </div>

        <button
          disabled
          title="起票機能はフェーズ2で提供予定です"
          style={{
            alignSelf: "flex-start", padding: "8px 18px", borderRadius: "6px",
            border: `1px solid ${C.borderLight}`, background: "transparent",
            color: C.textDim, fontSize: "12px", fontWeight: 700, fontFamily: "inherit",
            cursor: "not-allowed",
          }}
        >
          ✉ この内容で起票する（フェーズ2で提供予定）
        </button>

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

// ── エラーカード（システム障害・棄却と区別） ─────────────────────────────────
function ErrorCard({ message }) {
  return (
    <div style={{
      padding: "12px 18px", borderRadius: "10px",
      background: C.redSoft, border: `1px solid ${C.red}`,
      fontSize: "12.5px", color: C.red, lineHeight: 1.8,
    }}>
      ✗ {message}
      <div style={{ fontSize: "11px", color: C.textMuted, marginTop: "2px" }}>
        ※ ナレッジの有無とは無関係のシステム障害です。時間をおいて再度お試しください。
      </div>
    </div>
  );
}

// ── メイン ───────────────────────────────────────────────────────────────────
export default function InquiryPage() {
  const [utility, setUtility] = useState("関東電力");
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState([]); // {id, question, status: loading|done|error, result?, error?, startedAt}

  const isLoading = thread.some((e) => e.status === "loading");

  const handleAsk = async (text) => {
    const q = (text ?? question).trim();
    const u = utility.trim();
    if (!q || !u || isLoading) return;

    const id = ++entrySeq;
    setQuestion("");
    // 新しい質問を先頭に（入力欄の直下に最新の結果が来る）
    setThread((prev) => [{ id, question: q, status: "loading", startedAt: Date.now() }, ...prev]);

    const settle = (patch) =>
      setThread((prev) => prev.map((e) => (e.id === id ? { ...e, ...patch } : e)));

    try {
      const res = await fetch(`${API_BASE}/inquiry/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, utility: u }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        // FastAPI の detail は文字列（500/502）または配列（422）
        const detail = Array.isArray(err.detail)
          ? err.detail.map((d) => d.msg || JSON.stringify(d)).join(" / ")
          : err.detail;
        throw new Error(detail || `サーバーエラー（HTTP ${res.status}）`);
      }
      settle({ status: "done", result: await res.json() });
    } catch (e) {
      // fetch 失敗は TypeError（メッセージ文字列はブラウザ依存のため型で判定）
      settle({
        status: "error",
        error: e instanceof TypeError
          ? "バックエンドに接続できません。起動しているか確認してください。"
          : e.message,
      });
    }
  };

  const isAskDisabled = !question.trim() || !utility.trim() || isLoading;

  return (
    <div style={{
      display: "flex", flexDirection: "column", height: "100vh",
      background: C.bg, color: C.text,
      fontFamily: "'Noto Sans JP', 'Courier New', monospace",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&display=swap');
        @keyframes spin { to { transform: rotate(360deg); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2e42; border-radius: 3px; }
        textarea::placeholder { color: #4a5568; }
      `}</style>

      <AppHeader rightSlot="問い合わせ — F3自社ナレッジから引用付きで回答" />

      <div style={{ flex: 1, overflowY: "auto" }} aria-live="polite">
        <div style={{ maxWidth: "800px", margin: "0 auto", padding: "24px 24px 48px", display: "flex", flexDirection: "column", gap: "20px" }}>

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
              <span style={{ fontSize: "11px", color: C.textMuted, fontWeight: 600 }}>電力会社</span>
              <input
                value={utility}
                onChange={(e) => setUtility(e.target.value)}
                style={{
                  width: "150px", padding: "6px 10px", borderRadius: "6px",
                  border: `1px solid ${C.borderLight}`, background: C.bg,
                  color: C.text, fontSize: "12px", outline: "none", fontFamily: "inherit",
                }}
              />
              <span style={{ fontSize: "10px", color: C.textDim }}>自社のナレッジのみが検索対象です</span>
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
              {entry.status === "done" && entry.result.status === "answered" && <AnsweredCard result={entry.result} />}
              {entry.status === "done" && entry.result.status === "abstained" && (
                <AbstainedCard result={entry.result} question={entry.question} />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
