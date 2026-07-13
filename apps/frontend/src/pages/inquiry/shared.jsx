/**
 * 問い合わせ3画面（質問・一覧・詳細）の共通部品（DESIGN §2）
 *
 * - カラートークン C は App.jsx / ReviewPage.jsx と同一のデザイン言語
 * - useIdentity：PoC の簡易ユーザー識別（REQUIREMENTS §9-2 未確定のための暫定・D-16）。
 *   電力／NuRO のロールと表示名を localStorage に保持する。本番移行時は認証由来の
 *   ユーザー情報に差し替え、IdentityBar を撤去する想定。
 * - InquiryShell：ヘッダー＋画面内ナビ＋ユーザー設定バーの共通レイアウト
 */
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AppHeader } from "../../App.jsx";

// App.jsx と同一のカラートークン
export const C = {
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

export const DIRECTION_LABELS = { nuro: "NuRO確認", denryoku: "電力回答" };

// 棄却理由 → 表示（DESIGN §6）
export const ABSTAIN_INFO = {
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

// 問い合わせステータス → 表示（DESIGN §1-3）
export const STATUS_INFO = {
  open: { label: "未回答", color: C.warning, soft: C.warningSoft },
  answered: { label: "回答済", color: C.accent, soft: C.accentSoft },
  resolved: { label: "解決", color: C.success, soft: C.successSoft },
};

/** ISO文字列 → "2026/07/13 23:49" 形式（一覧・詳細で共用） */
export function formatTimestamp(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("ja-JP", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

export function Spinner({ size = 14 }) {
  return (
    <span style={{
      width: size, height: size, display: "inline-block", flexShrink: 0,
      border: "2px solid rgba(255,255,255,0.25)", borderTop: `2px solid ${C.accent}`,
      borderRadius: "50%", animation: "spin 0.8s linear infinite",
    }} />
  );
}

export function StatusBadge({ status }) {
  const info = STATUS_INFO[status] || STATUS_INFO.open;
  return (
    <span style={{
      padding: "2px 10px", borderRadius: "4px", fontSize: "11px", fontWeight: 700,
      color: info.color, background: info.soft, border: `1px solid ${info.color}`,
      whiteSpace: "nowrap",
    }}>
      {info.label}
    </span>
  );
}

// ── エラーカード（システム障害・棄却と区別。DESIGN §6） ─────────────────────
export function ErrorCard({ message }) {
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

// ── 根拠・関連ナレッジカード ─────────────────────────────────────────────────
export function EvidenceCard({ ev, num, highlighted, onHover, cardRef }) {
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

// ── PoC の簡易ユーザー識別（D-16・localStorage 保持） ────────────────────────
const IDENTITY_KEY = "nuro.inquiry.identity";
const DEFAULT_IDENTITY = { role: "denryoku", utility: "関東電力", displayName: "関東電力 担当者" };

// localStorage は外部から書き換わり得るため、型が合うキーだけ採用する
// （null 等が混ざると identity.utility.trim() で描画クラッシュするのを防ぐ）
function sanitizeIdentity(stored) {
  const identity = { ...DEFAULT_IDENTITY };
  if (stored && typeof stored === "object") {
    if (stored.role === "denryoku" || stored.role === "nuro") identity.role = stored.role;
    if (typeof stored.utility === "string") identity.utility = stored.utility;
    if (typeof stored.displayName === "string") identity.displayName = stored.displayName;
  }
  return identity;
}

export function useIdentity() {
  const [identity, setIdentity] = useState(() => {
    try {
      return sanitizeIdentity(JSON.parse(localStorage.getItem(IDENTITY_KEY) || "{}"));
    } catch {
      return DEFAULT_IDENTITY;
    }
  });
  const update = (patch) => {
    setIdentity((prev) => {
      const next = { ...prev, ...patch };
      localStorage.setItem(IDENTITY_KEY, JSON.stringify(next));
      return next;
    });
  };
  return [identity, update];
}

function IdentityBar({ identity, onChange }) {
  const inputStyle = {
    padding: "5px 10px", borderRadius: "6px",
    border: `1px solid ${C.borderLight}`, background: C.bg,
    color: C.text, fontSize: "12px", outline: "none", fontFamily: "inherit",
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
      <div style={{ display: "flex", borderRadius: "6px", overflow: "hidden", border: `1px solid ${C.borderLight}` }}>
        {[
          { key: "denryoku", label: "電力ユーザー" },
          { key: "nuro", label: "NuRO担当者" },
        ].map(({ key, label }) => {
          const active = identity.role === key;
          return (
            <button
              key={key}
              onClick={() => onChange({ role: key })}
              style={{
                padding: "5px 12px", border: "none", fontSize: "11px", fontWeight: 700,
                fontFamily: "inherit", cursor: "pointer", transition: "all 0.15s",
                color: active ? "#fff" : C.textMuted,
                background: active ? C.accent : "transparent",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
      {identity.role === "denryoku" && (
        <>
          <span style={{ fontSize: "11px", color: C.textMuted, fontWeight: 600 }}>電力会社</span>
          <input
            value={identity.utility}
            onChange={(e) => onChange({ utility: e.target.value })}
            style={{ ...inputStyle, width: "120px" }}
          />
        </>
      )}
      <span style={{ fontSize: "11px", color: C.textMuted, fontWeight: 600 }}>表示名</span>
      <input
        value={identity.displayName}
        onChange={(e) => onChange({ displayName: e.target.value })}
        style={{ ...inputStyle, width: "160px" }}
        placeholder="起票者・回答者として記録されます"
      />
      <span style={{ fontSize: "10px", color: C.textDim }}>
        ※ PoC簡易運用（認証なし・REQUIREMENTS §9-2）
      </span>
    </div>
  );
}

// ── 3画面共通レイアウト ──────────────────────────────────────────────────────
export function InquiryShell({ rightSlot, identity, onIdentityChange, maxWidth = "800px", children }) {
  const navigate = useNavigate();
  const location = useLocation();

  const subTabs = [
    { path: "/inquiry", label: "🔎 質問する", exact: true },
    { path: "/inquiry/tickets", label: "📋 問い合わせ一覧", exact: false },
  ];

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
        textarea::placeholder, input::placeholder { color: #4a5568; }
      `}</style>

      <AppHeader rightSlot={rightSlot} />

      {/* 画面内ナビ＋ユーザー設定バー */}
      <div style={{
        padding: "10px 24px", borderBottom: `1px solid ${C.border}`, background: C.surface,
        display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", gap: "6px" }}>
          {subTabs.map(({ path, label, exact }) => {
            const active = exact
              ? location.pathname === path
              : location.pathname.startsWith(path);
            return (
              <button
                key={path}
                onClick={() => navigate(path)}
                style={{
                  padding: "5px 14px", borderRadius: "6px", fontSize: "12px",
                  fontWeight: active ? 700 : 400, fontFamily: "inherit", cursor: "pointer",
                  border: `1px solid ${active ? C.accent : C.border}`,
                  background: active ? C.accentSoft : "transparent",
                  color: active ? C.accent : C.textMuted,
                  transition: "all 0.15s",
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
        <div style={{ marginLeft: "auto" }}>
          <IdentityBar identity={identity} onChange={onIdentityChange} />
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto" }} aria-live="polite">
        <div style={{
          maxWidth, margin: "0 auto", padding: "24px 24px 48px",
          display: "flex", flexDirection: "column", gap: "20px",
        }}>
          {children}
        </div>
      </div>
    </div>
  );
}
