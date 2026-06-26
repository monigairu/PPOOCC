import { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";

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
  warning: "#fbbf24",
  warningSoft: "rgba(251,191,36,0.12)",
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
  centerPanel: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
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

// ── 共通ナビゲーションヘッダー ─────────────────
export function AppHeader({ rightSlot }) {
  const navigate = useNavigate();
  const location = useLocation();
  const currentPath = location.pathname;

  const tabs = [
    { path: "/",       label: "様式自動作成①" },
    { path: "/review", label: "事前レビュー" },
  ];

  return (
    <header style={styles.header}>
      <span style={styles.headerBadge}>PoC</span>
      <h1 style={styles.headerTitle}>NuRO</h1>

      <div style={{ display: "flex", gap: "4px", marginLeft: "12px" }}>
        {tabs.map(({ path, label }) => {
          const isActive = currentPath === path;
          return (
            <button
              key={path}
              onClick={() => navigate(path)}
              style={{
                padding: "5px 14px",
                borderRadius: "6px",
                border: `1px solid ${isActive ? COLORS.accent : COLORS.border}`,
                background: isActive ? COLORS.accentSoft : "transparent",
                color: isActive ? COLORS.accent : COLORS.textMuted,
                fontSize: "12px",
                fontWeight: isActive ? 700 : 400,
                cursor: "pointer",
                transition: "all 0.15s",
                fontFamily: "inherit",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      <span style={styles.headerSub}>{rightSlot}</span>
    </header>
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
      display: "inline-block",
    }} />
  );
}

// ── セッション履歴サイドバー ────────────────────
function SessionHistorySidebar({ sessions, selectedSessionId, onSelect, onNewClick, isLoading }) {
  const SessionItem = ({ session }) => {
    const isSelected = selectedSessionId === session.session_id;
    const label = session.session_name || session.utility_name || "未設定";

    return (
      <div
        onClick={() => onSelect(session)}
        style={{
          padding: "9px 14px",
          cursor: "pointer",
          background: isSelected ? COLORS.accentSoft : "transparent",
          borderLeft: `3px solid ${isSelected ? COLORS.accent : "transparent"}`,
          borderBottom: `1px solid ${COLORS.border}`,
          transition: "background 0.12s",
        }}
        onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = COLORS.surfaceHover; }}
        onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
      >
        <div style={{
          fontSize: "12px",
          color: isSelected ? COLORS.accent : COLORS.text,
          fontWeight: 600,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          marginBottom: "3px",
        }}>
          {label}
        </div>
        <div style={{ fontSize: "10px", color: COLORS.textDim }}>
          {session.created_at
            ? new Date(session.created_at).toLocaleDateString("ja-JP", { month: "2-digit", day: "2-digit" })
            : ""}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 新規転記ボタン */}
      <div style={{ padding: "12px 14px", borderBottom: `1px solid ${COLORS.border}` }}>
        <button
          onClick={onNewClick}
          style={{
            width: "100%",
            padding: "8px",
            borderRadius: "6px",
            border: `1px solid ${COLORS.accent}`,
            background: COLORS.accentSoft,
            color: COLORS.accent,
            fontSize: "12px",
            fontWeight: 700,
            cursor: "pointer",
            fontFamily: "inherit",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "6px",
          }}
        >
          + 新規転記
        </button>
      </div>

      {/* セッション一覧（フラットリスト・新しい順） */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {isLoading ? (
          <div style={{ padding: "24px", textAlign: "center", color: COLORS.textDim, fontSize: "12px", display: "flex", flexDirection: "column", alignItems: "center", gap: "8px" }}>
            <Spinner />
            <span>読み込み中...</span>
          </div>
        ) : sessions.length === 0 ? (
          <div style={{ padding: "20px 14px", fontSize: "11px", color: COLORS.textDim }}>
            転記履歴がありません
          </div>
        ) : (
          sessions.map(s => <SessionItem key={s.session_id} session={s} />)
        )}
      </div>
    </div>
  );
}

// ── アップロードゾーン（複数ファイル対応） ────
function UploadZone({ onFilesSelect, files, isLoading }) {
  const inputRef = useRef(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    const dropped = Array.from(e.dataTransfer.files);
    if (dropped.length > 0) onFilesSelect(dropped);
  };

  const handleChange = (e) => {
    const selected = Array.from(e.target.files);
    if (selected.length > 0) onFilesSelect(selected);
    // 同じファイルを再選択できるようリセット
    e.target.value = "";
  };

  return (
    <div style={{ padding: "16px", flex: 1, display: "flex", flexDirection: "column", gap: "12px" }}>
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
          複数ファイル可 | .xlsx / .xls / .docx / .pdf
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls,.docx,.pdf"
          multiple
          style={{ display: "none" }}
          onChange={handleChange}
        />
      </div>

      {files.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          {files.map((f, i) => (
            <div key={i} style={{
              padding: "7px 10px",
              borderRadius: "5px",
              background: COLORS.successSoft,
              border: `1px solid ${COLORS.success}`,
              fontSize: "11px",
              color: COLORS.success,
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}>
              <span>✓</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                {f.name}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onFilesSelect(files.filter((_, j) => j !== i)); }}
                style={{ background: "none", border: "none", color: COLORS.success, cursor: "pointer", fontSize: "12px", padding: "0 2px", opacity: 0.7 }}
              >×</button>
            </div>
          ))}
        </div>
      )}

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

      <div style={{
        padding: "10px 12px",
        borderRadius: "6px",
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${COLORS.border}`,
        fontSize: "11px",
        color: COLORS.textDim,
        lineHeight: 1.7,
      }}>
        複数ファイルを同時にアップロードできます。<br />
        AIが各資料からフィールドを抽出・統合し MRC1 に転記します。
      </div>
    </div>
  );
}

// ── 実行ボタン ────────────────────────────────
function RunButton({ onClick, isLoading, disabled, progress }) {
  const label = isLoading
    ? progress > 0 ? `処理中... ${progress}%` : "AI転記実行中..."
    : "▶  様式の作成を開始";

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
          fontFamily: "inherit",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {isLoading && progress > 0 && (
          <div style={{
            position: "absolute", left: 0, top: 0, bottom: 0,
            width: `${progress}%`,
            background: "rgba(255,255,255,0.12)",
            transition: "width 0.5s",
          }} />
        )}
        {isLoading ? <><Spinner />{label}</> : label}
      </button>
    </div>
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
        <div style={{ fontSize: "13px" }}>左のサイドバーからセッションを選択するか</div>
        <div style={{ fontSize: "13px" }}>新規転記を実行してください</div>
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

// ── ユーティリティ: 列インデックス→列文字 ──────
function colIdxToLetter(n) {
  let s = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

// ── Excelグリッドビュー ────────────────────────
function ExcelGridView({ mappings, onCellClick, selectedCell, template, templateError }) {
  const [hoveredAddr, setHoveredAddr] = useState(null);

  if (templateError) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#f87171", fontSize: "13px" }}>
        テンプレート読み込みエラー: {templateError}
      </div>
    );
  }

  if (!template) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: "10px", color: COLORS.textDim }}>
        <Spinner />
        <span style={{ fontSize: "13px" }}>テンプレート読み込み中...</span>
      </div>
    );
  }

  const mappingMap = {};
  (mappings || []).forEach((m) => { mappingMap[m.cell_address] = m; });

  const { max_row, max_col, cells, merged_cells, col_widths, row_heights } = template;

  const grid = Array.from({ length: max_row }, (_, r) =>
    Array.from({ length: max_col }, (_, c) => ({
      row: r + 1, col: c + 1,
      address: colIdxToLetter(c + 1) + (r + 1),
      value: null, isCovered: false, rowspan: 1, colspan: 1,
    }))
  );

  cells.forEach((cell) => {
    if (cell.row <= max_row && cell.col <= max_col)
      grid[cell.row - 1][cell.col - 1].value = cell.value;
  });

  merged_cells.forEach((m) => {
    const sr = m.start_row - 1, sc = m.start_col - 1;
    if (sr < max_row && sc < max_col) {
      grid[sr][sc].rowspan = m.end_row - m.start_row + 1;
      grid[sr][sc].colspan = m.end_col - m.start_col + 1;
      for (let r = m.start_row; r <= Math.min(m.end_row, max_row); r++)
        for (let c = m.start_col; c <= Math.min(m.end_col, max_col); c++)
          if (r !== m.start_row || c !== m.start_col)
            grid[r - 1][c - 1].isCovered = true;
    }
  });

  return (
    <div style={{ flex: 1, overflow: "auto", padding: "8px 12px" }}>
      {Object.keys(mappingMap).length > 0 && (
        <div style={{ display: "flex", gap: "16px", marginBottom: "8px", fontSize: "11px", color: COLORS.textMuted, alignItems: "center" }}>
          <span style={{ display: "flex", alignItems: "center", gap: "5px" }}>
            <span style={{ display: "inline-block", width: "12px", height: "12px", borderRadius: "2px", background: "rgba(52,211,153,0.25)", border: "1px solid #34d399" }} />
            転記済みセル（クリックで根拠確認）
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "5px" }}>
            <span style={{ display: "inline-block", width: "12px", height: "12px", borderRadius: "2px", background: "rgba(79,142,247,0.35)", border: `1px solid ${COLORS.accent}` }} />
            選択中
          </span>
        </div>
      )}
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", tableLayout: "fixed", fontSize: "11px", minWidth: "max-content" }}>
          <colgroup>
            {Array.from({ length: max_col }, (_, i) => (
              <col key={i} style={{ width: (col_widths[String(i + 1)] || 80) + "px" }} />
            ))}
          </colgroup>
          <tbody>
            {grid.map((row, rIdx) => (
              <tr key={rIdx} style={{ height: (row_heights[String(rIdx + 1)] || 20) + "px" }}>
                {row.map((cell, cIdx) => {
                  if (cell.isCovered) return null;
                  const mapping = mappingMap[cell.address];
                  const isSelected = selectedCell?.cell_address === cell.address;
                  const isHovered = hoveredAddr === cell.address;

                  let bg = "transparent", textColor = COLORS.textMuted, borderStyle = `1px solid ${COLORS.border}`;
                  if (mapping) {
                    if (isSelected) {
                      bg = "rgba(79,142,247,0.35)"; borderStyle = `2px solid ${COLORS.accent}`; textColor = "#fff";
                    } else if (isHovered) {
                      bg = "rgba(52,211,153,0.30)"; borderStyle = `1px solid ${COLORS.success}`; textColor = COLORS.success;
                    } else {
                      bg = "rgba(52,211,153,0.15)"; borderStyle = `1px solid rgba(52,211,153,0.4)`; textColor = COLORS.success;
                    }
                  }

                  return (
                    <td
                      key={cIdx}
                      rowSpan={cell.rowspan}
                      colSpan={cell.colspan}
                      onClick={() => mapping && onCellClick(mapping)}
                      onMouseEnter={() => mapping && setHoveredAddr(cell.address)}
                      onMouseLeave={() => setHoveredAddr(null)}
                      title={mapping ? `${mapping.field_name}（${cell.address}）\n転記値: ${mapping.value}` : undefined}
                      style={{
                        border: borderStyle, background: bg,
                        cursor: mapping ? "pointer" : "default",
                        padding: "2px 5px", color: textColor,
                        fontWeight: mapping ? 700 : 400,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        transition: "background 0.1s, border 0.1s",
                        verticalAlign: "middle", maxWidth: 0,
                      }}
                    >
                      {mapping ? mapping.value : cell.value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── チャットパネル ─────────────────────────────
function ChatPanel({ selectedCell, sessionId, frameName, onCellEdit }) {
  const [messages, setMessages] = useState([
    {
      role: "ai",
      text: "様式自動作成AIです。\n転記結果テーブルの行をクリックすると、そのセルについて質問したり、値の変更を依頼できます。",
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    if (!selectedCell) return;
    setMessages((prev) => [
      ...prev,
      {
        role: "ai",
        text: `「${selectedCell.field_name}」（${selectedCell.cell_address}）が選択されました。\n\n転記した値: **${selectedCell.value}**\n\nこのセルについて質問したり、値の変更を依頼できます。`,
      },
    ]);
  }, [selectedCell]);

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
          session_id: sessionId || "",
          message: userMessage,
          cell_address: selectedCell.cell_address,
          field_name: selectedCell.field_name,
          field_value: selectedCell.value,
          reasoning: selectedCell.reasoning,
          frame_name: frameName || "frameB",
          sheet_name: "MRC1",
        }),
      });
      const data = await res.json();

      // 編集成功時: テーブルを楽観的更新してバッジ付きメッセージを表示
      if (data.type === "edited" && data.edited_cells?.length > 0) {
        onCellEdit?.(data.edited_cells);
        setMessages((prev) => [
          ...prev,
          { role: "ai", text: data.answer, type: "edited" },
        ]);
      } else {
        setMessages((prev) => [...prev, { role: "ai", text: data.answer }]);
      }
    } catch {
      setMessages((prev) => [...prev, { role: "ai", text: "エラーが発生しました。バックエンドが起動しているか確認してください。" }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
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

      <div style={{ flex: 1, overflow: "auto", padding: "16px", display: "flex", flexDirection: "column", gap: "12px" }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            display: "flex",
            flexDirection: m.role === "user" ? "row-reverse" : "row",
            gap: "8px",
            alignItems: "flex-start",
          }}>
            <div style={{
              width: "28px", height: "28px", borderRadius: "50%", flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "13px",
              background: m.role === "user" ? COLORS.userBubble : COLORS.accentSoft,
              border: `1px solid ${m.role === "user" ? "#2a4a7f" : COLORS.borderLight}`,
            }}>
              {m.role === "user" ? "👤" : "🤖"}
            </div>
            <div style={{
              maxWidth: "80%",
              padding: "10px 13px",
              borderRadius: m.role === "user" ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
              background: m.role === "user" ? COLORS.userBubble
                : m.type === "edited" ? "rgba(52,211,153,0.08)"
                : COLORS.aiBubble,
              border: `1px solid ${m.role === "user" ? "#2a4a7f"
                : m.type === "edited" ? "rgba(52,211,153,0.4)"
                : COLORS.border}`,
              fontSize: "13px",
              lineHeight: 1.7,
              color: COLORS.text,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}>
              {m.type === "edited" && (
                <span style={{
                  display: "inline-block",
                  marginBottom: "4px",
                  padding: "1px 7px",
                  borderRadius: "3px",
                  background: COLORS.success,
                  color: "#0a1a12",
                  fontSize: "10px",
                  fontWeight: 700,
                  letterSpacing: "0.05em",
                }}>
                  変更完了
                </span>
              )}
              {m.type === "edited" && "\n"}{m.text}
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
          placeholder={selectedCell ? "質問や変更指示を入力（例: 電力会社を北の海電力に変えて）" : "左のテーブルから行を選択してください"}
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
  // 転記結果
  const [files, setFiles]           = useState([]);
  const [isLoading, setIsLoading]   = useState(false);
  const [progress, setProgress]     = useState(0);
  const [jobId, setJobId]           = useState(null);
  const [mappings, setMappings]     = useState([]);
  const [conflicts, setConflicts]   = useState([]);
  const [skippedCells, setSkippedCells] = useState([]);
  const [sessionId, setSessionId]   = useState(null);
  const [frameName, setFrameName]   = useState("frameB");
  const [selectedCell, setSelectedCell] = useState(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError]           = useState("");
  const [viewMode, setViewMode]     = useState("table");

  // テンプレート
  const [template, setTemplate]         = useState(null);
  const [templateError, setTemplateError] = useState(null);
  const [resultTemplate, setResultTemplate] = useState(null);

  // 履歴サイドバー
  const [leftMode, setLeftMode]               = useState("history"); // "history" | "upload"
  const [sessions, setSessions]               = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [selectedSession, setSelectedSession] = useState(null);

  // 空テンプレートは起動時に一度だけ取得
  useEffect(() => {
    fetch(`${API_BASE}/template?sheet_name=MRC1`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setTemplate)
      .catch((e) => setTemplateError(e.message));
    fetchSessions();
  }, []);

  const fetchSessions = async () => {
    setSessionsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/sessions`);
      if (res.ok) setSessions(await res.json());
    } catch {
      // サイドバー取得失敗は無視
    } finally {
      setSessionsLoading(false);
    }
  };

  // 履歴セッション選択
  const handleSelectSession = async (session) => {
    setSelectedSession(session);
    setSelectedCell(null);
    setStatusMessage("");
    setError("");
    setMappings([]);
    setResultTemplate(null);

    try {
      const res = await fetch(`${API_BASE}/sessions/${session.session_id}/mappings`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMappings(data.mappings);
      setSessionId(data.session_id);
      const fn = data.frame_name || "frameB";
      setFrameName(fn);

      fetch(`${API_BASE}/result-layout/${data.session_id}?frame_name=${fn}&sheet_name=MRC1`)
        .then(r => r.ok ? r.json() : null)
        .then(layout => { if (layout) setResultTemplate(layout); })
        .catch(() => {});
    } catch (e) {
      setError(e.message);
    }
  };

  // 新規転記モードに切替
  const handleNewClick = () => {
    setLeftMode("upload");
    setSelectedSession(null);
    setMappings([]);
    setConflicts([]);
    setSkippedCells([]);
    setSessionId(null);
    setJobId(null);
    setSelectedCell(null);
    setStatusMessage("");
    setError("");
    setResultTemplate(null);
    setFiles([]);
    setProgress(0);
  };

  // 履歴モードに戻る
  const handleBackToHistory = () => {
    setLeftMode("history");
    fetchSessions();
  };

  // 新規転記実行（N対1・非同期ジョブ方式）
  const handleRun = async () => {
    if (files.length === 0) return;
    setIsLoading(true);
    setError("");
    setMappings([]);
    setConflicts([]);
    setSkippedCells([]);
    setSelectedCell(null);
    setProgress(0);
    setStatusMessage(`${files.length} 件のファイルを送信中...`);

    try {
      // ── STEP 1: ジョブを登録して job_id を取得 ──
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      formData.append("sheet", "MRC1");
      formData.append("frame", "frameB");

      const res = await fetch(`${API_BASE}/transcribe/mrc1`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "転記ジョブの登録に失敗しました");
      }

      const { job_id } = await res.json();
      setJobId(job_id);
      setStatusMessage("AIが転記中（複数ファイル処理中）...");

      // ── STEP 2: ポーリング（2秒間隔・最大120秒）──
      const POLL_INTERVAL = 2000;
      const TIMEOUT = 120_000;
      const startTime = Date.now();

      await new Promise((resolve, reject) => {
        const poll = async () => {
          if (Date.now() - startTime > TIMEOUT) {
            reject(new Error("タイムアウト: 転記処理が120秒以内に完了しませんでした"));
            return;
          }

          const statusRes = await fetch(`${API_BASE}/jobs/${job_id}`);
          if (!statusRes.ok) { reject(new Error("ジョブ状態の取得に失敗しました")); return; }

          const job = await statusRes.json();
          setProgress(job.progress ?? 0);

          if (job.status === "completed") {
            const result = job.result;
            setMappings(result.cell_mappings || []);
            setConflicts(result.conflicts || []);
            setSkippedCells(result.skipped_cells || []);
            setJobId(job_id);
            setSelectedSession(null);
            setStatusMessage(`転記完了（${files.length} ファイル）`);
            resolve();
          } else if (job.status === "failed") {
            reject(new Error(job.error || "転記処理が失敗しました"));
          } else {
            setTimeout(poll, POLL_INTERVAL);
          }
        };
        poll();
      });

    } catch (e) {
      setError(e.message);
      setStatusMessage("");
    } finally {
      setIsLoading(false);
      setProgress(0);
    }
  };

  // ヘッダー右スロット: ダウンロードリンクを表示
  const isCompleted = selectedSession?.review_status === "completed";
  const showSessionDownload = sessionId && leftMode === "history" && isCompleted;
  const showJobDownload = jobId && leftMode === "upload" && mappings.length > 0;
  const headerRight = showJobDownload
    ? <a href={`${API_BASE}/download-job/${jobId}`}
         style={{ color: COLORS.accent, textDecoration: "none", fontSize: "12px" }}>
        ⬇ Excelをダウンロード
      </a>
    : showSessionDownload
    ? <a href={`${API_BASE}/download/${sessionId}?frame_name=${frameName}`}
         style={{ color: COLORS.accent, textDecoration: "none", fontSize: "12px" }}>
        ⬇ Excelをダウンロード
      </a>
    : <span style={{ fontSize: "12px", color: COLORS.textMuted }}>MRC1 — 計実_様式2_PBG_工事概要①</span>;

  return (
    <div style={styles.app}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&display=swap');
        @keyframes spin { to { transform: rotate(360deg); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2e42; border-radius: 3px; }
      `}</style>

      <AppHeader rightSlot={headerRight} />

      <div style={styles.body}>
        {/* ── 左パネル ── */}
        <div style={styles.leftPanel}>
          {leftMode === "history" ? (
            <SessionHistorySidebar
              sessions={sessions}
              selectedSessionId={selectedSession?.session_id}
              onSelect={handleSelectSession}
              onNewClick={handleNewClick}
              isLoading={sessionsLoading}
            />
          ) : (
            <>
              {/* 新規転記モード: 戻るボタン + アップロードゾーン */}
              <div style={{
                padding: "10px 14px",
                borderBottom: `1px solid ${COLORS.border}`,
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}>
                <button
                  onClick={handleBackToHistory}
                  style={{
                    padding: "5px 10px",
                    borderRadius: "5px",
                    border: `1px solid ${COLORS.border}`,
                    background: "transparent",
                    color: COLORS.textMuted,
                    fontSize: "11px",
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  ← 履歴
                </button>
                <span style={{ fontSize: "11px", fontWeight: 700, color: COLORS.textMuted, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                  新規転記
                </span>
              </div>
              <UploadZone onFilesSelect={setFiles} files={files} isLoading={isLoading} />
              <RunButton onClick={handleRun} isLoading={isLoading} disabled={files.length === 0} progress={progress} />
            </>
          )}
        </div>

        {/* ── 中央パネル ── */}
        <div style={styles.centerPanel}>
          <div style={{
            ...styles.panelHeader,
            display: "flex",
            alignItems: "center",
            gap: "12px",
            background: COLORS.surface,
            borderBottom: `1px solid ${COLORS.border}`,
          }}>
            <span>転記結果</span>
            {mappings.length > 0 && (
              <div style={{ display: "flex", gap: "4px" }}>
                {[{ key: "table", label: "テーブル" }, { key: "grid", label: "様式プレビュー" }].map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setViewMode(key)}
                    style={{
                      padding: "3px 10px", borderRadius: "4px",
                      border: `1px solid ${viewMode === key ? COLORS.accent : COLORS.border}`,
                      background: viewMode === key ? COLORS.accentSoft : "transparent",
                      color: viewMode === key ? COLORS.accent : COLORS.textMuted,
                      cursor: "pointer", fontSize: "11px", fontWeight: 600,
                      textTransform: "none", letterSpacing: 0, transition: "all 0.15s",
                      fontFamily: "inherit",
                    }}
                  >{label}</button>
                ))}
              </div>
            )}
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
                {viewMode === "grid"
                  ? "緑のセルをクリックすると根拠をチャットで確認できます"
                  : "行をクリックすると右のチャットで根拠を確認できます"}
              </span>
            )}
          </div>
          {/* 競合・スキップ情報バナー */}
          {(conflicts.length > 0 || skippedCells.length > 0) && (
            <div style={{ padding: "8px 16px", display: "flex", gap: "10px", flexWrap: "wrap", borderBottom: `1px solid ${COLORS.border}`, background: "rgba(0,0,0,0.2)" }}>
              {conflicts.length > 0 && (
                <div style={{
                  padding: "5px 12px", borderRadius: "5px", fontSize: "11px",
                  background: COLORS.warningSoft, border: `1px solid ${COLORS.warning}`,
                  color: COLORS.warning, fontWeight: 600,
                }}>
                  ⚠ 競合 {conflicts.length} 件（複数ファイルで値が異なるフィールドがあります）
                </div>
              )}
              {skippedCells.length > 0 && (
                <div style={{
                  padding: "5px 12px", borderRadius: "5px", fontSize: "11px",
                  background: "rgba(148,163,184,0.1)", border: `1px solid ${COLORS.borderLight}`,
                  color: COLORS.textMuted,
                }}>
                  数式セルのためスキップ: {skippedCells.join(", ")}
                </div>
              )}
            </div>
          )}

          {viewMode === "table" ? (
            <MappingTable
              mappings={mappings}
              onCellClick={setSelectedCell}
              selectedCell={selectedCell}
            />
          ) : (
            <ExcelGridView
              mappings={mappings}
              onCellClick={setSelectedCell}
              selectedCell={selectedCell}
              template={resultTemplate || template}
              templateError={templateError}
            />
          )}
        </div>

        {/* ── 右パネル: チャット ── */}
        <div style={styles.rightPanel}>
          <div style={styles.panelHeader}>AIチャット — 質問・編集</div>
          <ChatPanel
            selectedCell={selectedCell}
            sessionId={sessionId}
            frameName={frameName}
            onCellEdit={(editedCells) => {
              setMappings((prev) => prev.map((m) => {
                const edited = editedCells.find((e) => e.field_name === m.field_name);
                return edited ? { ...m, value: edited.new_value } : m;
              }));
            }}
          />
        </div>
      </div>
    </div>
  );
}
