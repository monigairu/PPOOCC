import { useState, useRef, useEffect } from "react";

// ── 定数 ──────────────────────────────────────
const API_BASE = "http://localhost:8000/api";

const COLORS = {
  bg: "#0f1117",
  surface: "#1a1d27",
  surfaceHover: "#22263a",
  border: "#2a2e42",
  borderLight: "#363b55",
  accent: "#4f8ef7",
  accentSoft: "rgba(79,142,247,0.12)",
  accentGlow: "rgba(79,142,247,0.25)",
  success: "#34d399",
  successSoft: "rgba(52,211,153,0.1)",
  text: "#e2e8f0",
  textMuted: "#8892a4",
  textDim: "#4a5568",
  userBubble: "#1e3a5f",
  aiBubble: "#1a1d27",
};

// ── スタイル ───────────────────────────────────
const styles = {
  app: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    background: COLORS.bg,
    fontFamily: "'Noto Sans JP', 'Courier New', monospace",
    color: COLORS.text,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "12px 24px",
    borderBottom: `1px solid ${COLORS.border}`,
    background: COLORS.surface,
    flexShrink: 0,
  },
  headerBadge: {
    padding: "3px 10px",
    borderRadius: "4px",
    background: COLORS.accentSoft,
    border: `1px solid ${COLORS.accent}`,
    color: COLORS.accent,
    fontSize: "11px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
  },
  headerTitle: {
    fontSize: "15px",
    fontWeight: 600,
    color: COLORS.text,
    margin: 0,
  },
  headerSub: {
    fontSize: "12px",
    color: COLORS.textMuted,
    marginLeft: "auto",
  },
  body: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
    gap: 0,
  },
  // ── 左パネル ──
  leftPanel: {
    width: "260px",
    flexShrink: 0,
    borderRight: `1px solid ${COLORS.border}`,
    display: "flex",
    flexDirection: "column",
    background: COLORS.surface,
    overflow: "hidden",
  },
  panelHeader: {
    padding: "14px 18px",
    borderBottom: `1px solid ${COLORS.border}`,
    fontSize: "11px",
    fontWeight: 700,
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    color: COLORS.textMuted,
  },
  // ── 中央パネル ──
  centerPanel: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  // ── 右パネル ──
  rightPanel: {
    width: "320px",
    flexShrink: 0,
    borderLeft: `1px solid ${COLORS.border}`,
    display: "flex",
    flexDirection: "column",
    background: COLORS.surface,
    overflow: "hidden",
  },
};

// ── アップロードゾーン ─────────────────────────
function UploadZone({ onFileSelect, file, isLoading }) {
  const inputRef = useRef(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) onFileSelect(f);
  };

  return (
    <div style={{ padding: "16px", flex: 1, display: "flex", flexDirection: "column", gap: "12px" }}>
      {/* ドラッグ&ドロップゾーン */}
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        style={{
          border: `2px dashed ${isDragOver ? COLORS.accent : COLORS.borderLight}`,
          borderRadius: "8px",
          padding: "24px 16px",
          textAlign: "center",
          cursor: "pointer",
          background: isDragOver ? COLORS.accentSoft : "transparent",
          transition: "all 0.2s",
        }}
      >
        <div style={{ fontSize: "24px", marginBottom: "8px" }}>📂</div>
        <div style={{ fontSize: "12px", color: COLORS.textMuted, lineHeight: 1.6 }}>
          資料ファイルをドロップ<br />またはクリックして選択
        </div>
        <div style={{ fontSize: "10px", color: COLORS.textDim, marginTop: "4px" }}>
          対応形式: .xlsx / .docx / .json
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".json,.xlsx,.xls,.docx"
          style={{ display: "none" }}
          onChange={(e) => e.target.files[0] && onFileSelect(e.target.files[0])}
        />
      </div>

      {/* 選択済みファイル表示 */}
      {file && (
        <div style={{
          padding: "10px 12px",
          borderRadius: "6px",
          background: COLORS.successSoft,
          border: `1px solid ${COLORS.success}`,
          fontSize: "12px",
          color: COLORS.success,
          display: "flex",
          alignItems: "center",
          gap: "8px",
        }}>
          <span>✓</span>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {file.name}
          </span>
        </div>
      )}

      {/* シート選択 */}
      <div>
        <div style={{ fontSize: "11px", color: COLORS.textMuted, marginBottom: "6px", fontWeight: 600 }}>
          対象シート
        </div>
        <div style={{
          padding: "8px 12px",
          borderRadius: "6px",
          background: COLORS.accentSoft,
          border: `1px solid ${COLORS.accent}`,
          fontSize: "13px",
          color: COLORS.accent,
          fontWeight: 600,
        }}>
          MRC1 — 工事概要①
        </div>
      </div>

      {/* 説明テキスト */}
      <div style={{
        padding: "10px 12px",
        borderRadius: "6px",
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${COLORS.border}`,
        fontSize: "11px",
        color: COLORS.textDim,
        lineHeight: 1.7,
      }}>
        アップロード後、AIが各フィールドを
        適切なセルに自動マッピングします。
        根拠はチャットで確認できます。
      </div>
    </div>
  );
}

// ── 実行ボタン ────────────────────────────────
function RunButton({ onClick, isLoading, disabled }) {
  return (
    <div style={{ padding: "16px", borderTop: `1px solid ${COLORS.border}` }}>
      <button
        onClick={onClick}
        disabled={disabled || isLoading}
        style={{
          width: "100%",
          padding: "11px",
          borderRadius: "8px",
          border: "none",
          background: disabled || isLoading
            ? COLORS.borderLight
            : `linear-gradient(135deg, ${COLORS.accent}, #6366f1)`,
          color: disabled || isLoading ? COLORS.textDim : "#fff",
          fontSize: "13px",
          fontWeight: 700,
          cursor: disabled || isLoading ? "not-allowed" : "pointer",
          letterSpacing: "0.05em",
          transition: "all 0.2s",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "8px",
        }}
      >
        {isLoading ? (
          <>
            <Spinner />
            AI転記実行中...
          </>
        ) : (
          "▶  様式の作成を開始"
        )}
      </button>
    </div>
  );
}

// ── スピナー ──────────────────────────────────
function Spinner() {
  return (
    <div style={{
      width: "14px",
      height: "14px",
      border: "2px solid rgba(255,255,255,0.3)",
      borderTop: "2px solid #fff",
      borderRadius: "50%",
      animation: "spin 0.8s linear infinite",
    }} />
  );
}

// ── 転記結果テーブル ───────────────────────────
function MappingTable({ mappings, onCellClick, selectedCell }) {
  if (!mappings || mappings.length === 0) {
    return (
      <div style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: "12px",
        color: COLORS.textDim,
      }}>
        <div style={{ fontSize: "40px", opacity: 0.3 }}>📋</div>
        <div style={{ fontSize: "13px" }}>資料ファイルをアップロードして</div>
        <div style={{ fontSize: "13px" }}>転記を実行してください</div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, overflow: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
        <thead>
          <tr style={{ background: COLORS.surface, position: "sticky", top: 0, zIndex: 1 }}>
            {["フィールド", "セル", "転記値", "根拠（クリックで詳細）"].map((h) => (
              <th key={h} style={{
                padding: "10px 14px",
                textAlign: "left",
                fontSize: "11px",
                fontWeight: 700,
                color: COLORS.textMuted,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                borderBottom: `1px solid ${COLORS.border}`,
                whiteSpace: "nowrap",
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {mappings.map((m, i) => {
            const isSelected = selectedCell?.cell_address === m.cell_address && selectedCell?.field_name === m.field_name;
            return (
              <tr
                key={i}
                onClick={() => onCellClick(m)}
                style={{
                  borderBottom: `1px solid ${COLORS.border}`,
                  background: isSelected ? COLORS.accentSoft : "transparent",
                  cursor: "pointer",
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = COLORS.surfaceHover; }}
                onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
              >
                <td style={{ padding: "10px 14px", color: COLORS.text, fontWeight: 500 }}>
                  {m.field_name}
                </td>
                <td style={{ padding: "10px 14px" }}>
                  <span style={{
                    padding: "2px 8px",
                    borderRadius: "4px",
                    background: COLORS.accentSoft,
                    border: `1px solid ${COLORS.accent}`,
                    color: COLORS.accent,
                    fontSize: "12px",
                    fontFamily: "monospace",
                    fontWeight: 700,
                  }}>
                    {m.cell_address}
                  </span>
                </td>
                <td style={{ padding: "10px 14px", color: COLORS.text, maxWidth: "150px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {m.value}
                </td>
                <td style={{ padding: "10px 14px", color: COLORS.textMuted, fontSize: "12px", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {isSelected
                    ? <span style={{ color: COLORS.accent }}>▶ チャットで確認中</span>
                    : m.reasoning}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── チャットパネル ─────────────────────────────
function ChatPanel({ selectedCell, sessionId }) {
  const [messages, setMessages] = useState([
    {
      role: "ai",
      text: "様式自動作成AIです。\n転記結果テーブルの行をクリックすると、そのセルについて質問できます。",
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const bottomRef = useRef(null);

  // セルが選択されたらAIから先行メッセージ
  useEffect(() => {
    if (!selectedCell) return;
    setMessages((prev) => [
      ...prev,
      {
        role: "ai",
        text: `「${selectedCell.field_name}」（${selectedCell.cell_address}）が選択されました。\n\n転記した値: **${selectedCell.value}**\n\nこのセルについて何か質問はありますか？`,
      },
    ]);
  }, [selectedCell]);

  // スクロール
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || !selectedCell) return;

    const userMessage = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: userMessage }]);
    setIsLoading(true);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userMessage,
          cell_address: selectedCell.cell_address,
          field_name: selectedCell.field_name,
          field_value: selectedCell.value,
          reasoning: selectedCell.reasoning,
        }),
      });
      const data = await res.json();
      setMessages((prev) => [...prev, { role: "ai", text: data.answer }]);
    } catch (e) {
      setMessages((prev) => [...prev, { role: "ai", text: "エラーが発生しました。バックエンドが起動しているか確認してください。" }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
      {/* 選択中セル表示 */}
      {selectedCell && (
        <div style={{
          padding: "10px 16px",
          borderBottom: `1px solid ${COLORS.border}`,
          background: COLORS.accentSoft,
          fontSize: "12px",
          display: "flex",
          gap: "8px",
          alignItems: "center",
        }}>
          <span style={{ color: COLORS.textMuted }}>選択中:</span>
          <span style={{
            padding: "1px 7px",
            borderRadius: "3px",
            background: COLORS.accent,
            color: "#fff",
            fontWeight: 700,
            fontSize: "11px",
            fontFamily: "monospace",
          }}>
            {selectedCell.cell_address}
          </span>
          <span style={{ color: COLORS.text, fontWeight: 600 }}>{selectedCell.field_name}</span>
        </div>
      )}

      {/* メッセージ一覧 */}
      <div style={{ flex: 1, overflow: "auto", padding: "16px", display: "flex", flexDirection: "column", gap: "12px" }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            display: "flex",
            flexDirection: m.role === "user" ? "row-reverse" : "row",
            gap: "8px",
            alignItems: "flex-start",
          }}>
            {/* アバター */}
            <div style={{
              width: "28px",
              height: "28px",
              borderRadius: "50%",
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "13px",
              background: m.role === "user" ? COLORS.userBubble : COLORS.accentSoft,
              border: `1px solid ${m.role === "user" ? "#2a4a7f" : COLORS.borderLight}`,
            }}>
              {m.role === "user" ? "👤" : "🤖"}
            </div>
            {/* バブル */}
            <div style={{
              maxWidth: "80%",
              padding: "10px 13px",
              borderRadius: m.role === "user" ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
              background: m.role === "user" ? COLORS.userBubble : COLORS.aiBubble,
              border: `1px solid ${m.role === "user" ? "#2a4a7f" : COLORS.border}`,
              fontSize: "13px",
              lineHeight: 1.7,
              color: COLORS.text,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}>
              {m.text}
            </div>
          </div>
        ))}
        {isLoading && (
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <div style={{
              width: "28px", height: "28px", borderRadius: "50%",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "13px", background: COLORS.accentSoft,
              border: `1px solid ${COLORS.borderLight}`,
            }}>🤖</div>
            <div style={{
              padding: "10px 16px", borderRadius: "4px 12px 12px 12px",
              background: COLORS.aiBubble, border: `1px solid ${COLORS.border}`,
              fontSize: "13px", color: COLORS.textMuted,
            }}>
              考えています...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 入力エリア */}
      <div style={{
        padding: "12px 16px",
        borderTop: `1px solid ${COLORS.border}`,
        display: "flex",
        gap: "8px",
      }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
          placeholder={selectedCell ? "このセルについて質問する..." : "左のテーブルから行を選択してください"}
          disabled={!selectedCell || isLoading}
          style={{
            flex: 1,
            padding: "9px 13px",
            borderRadius: "8px",
            border: `1px solid ${COLORS.borderLight}`,
            background: COLORS.bg,
            color: COLORS.text,
            fontSize: "13px",
            outline: "none",
            fontFamily: "inherit",
          }}
        />
        <button
          onClick={handleSend}
          disabled={!selectedCell || !input.trim() || isLoading}
          style={{
            padding: "9px 16px",
            borderRadius: "8px",
            border: "none",
            background: !selectedCell || !input.trim() ? COLORS.borderLight : COLORS.accent,
            color: !selectedCell || !input.trim() ? COLORS.textDim : "#fff",
            fontWeight: 700,
            fontSize: "13px",
            cursor: !selectedCell || !input.trim() ? "not-allowed" : "pointer",
            transition: "all 0.15s",
            whiteSpace: "nowrap",
          }}
        >
          送信
        </button>
      </div>
    </>
  );
}

// ── メインアプリ ───────────────────────────────
export default function App() {
  const [file, setFile] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [mappings, setMappings] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [selectedCell, setSelectedCell] = useState(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");

  const handleRun = async () => {
    if (!file) return;
    setIsLoading(true);
    setError("");
    setMappings([]);
    setSelectedCell(null);
    setStatusMessage("AIが転記中...");

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("sheet_name", "MRC1");
      formData.append("frame_name", "frameB");

      const res = await fetch(`${API_BASE}/upload`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "転記に失敗しました");
      }

      const data = await res.json();
      setMappings(data.mappings);
      setSessionId(data.session_id);
      setStatusMessage(data.message);
    } catch (e) {
      setError(e.message);
      setStatusMessage("");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={styles.app}>
      {/* CSS アニメーション */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&display=swap');
        @keyframes spin { to { transform: rotate(360deg); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2e42; border-radius: 3px; }
      `}</style>

      {/* ヘッダー */}
      <header style={styles.header}>
        <span style={styles.headerBadge}>PoC</span>
        <h1 style={styles.headerTitle}>NuRO 様式自動作成①</h1>
        <span style={styles.headerSub}>
          {sessionId
            ? <a href={`${API_BASE}/download/${sessionId}?sheet_name=MRC1`}
                 style={{ color: COLORS.accent, textDecoration: "none", fontSize: "12px" }}>
                ⬇ Excelをダウンロード
              </a>
            : "MRC1 — 計実_様式2_PBG_工事概要①"}
        </span>
      </header>

      <div style={styles.body}>
        {/* ── 左パネル: アップロード ── */}
        <div style={styles.leftPanel}>
          <div style={styles.panelHeader}>資料アップロード</div>
          <UploadZone onFileSelect={setFile} file={file} isLoading={isLoading} />
          <RunButton onClick={handleRun} isLoading={isLoading} disabled={!file} />
        </div>

        {/* ── 中央パネル: 転記結果 ── */}
        <div style={styles.centerPanel}>
          <div style={{
            ...styles.panelHeader,
            display: "flex",
            alignItems: "center",
            gap: "12px",
            background: COLORS.surface,
            borderBottom: `1px solid ${COLORS.border}`,
          }}>
            <span>転記結果テーブル</span>
            {statusMessage && (
              <span style={{ fontSize: "11px", color: COLORS.success, fontWeight: 600, textTransform: "none", letterSpacing: 0 }}>
                ✓ {statusMessage}
              </span>
            )}
            {error && (
              <span style={{ fontSize: "11px", color: "#f87171", fontWeight: 600, textTransform: "none", letterSpacing: 0 }}>
                ✗ {error}
              </span>
            )}
            {mappings.length > 0 && (
              <span style={{ fontSize: "11px", color: COLORS.textMuted, marginLeft: "auto", textTransform: "none", letterSpacing: 0 }}>
                行をクリックすると右のチャットで根拠を確認できます
              </span>
            )}
          </div>
          <MappingTable
            mappings={mappings}
            onCellClick={setSelectedCell}
            selectedCell={selectedCell}
          />
        </div>

        {/* ── 右パネル: チャット ── */}
        <div style={styles.rightPanel}>
          <div style={styles.panelHeader}>AIチャット — 根拠説明</div>
          <ChatPanel selectedCell={selectedCell} sessionId={sessionId} />
        </div>
      </div>
    </div>
  );
}