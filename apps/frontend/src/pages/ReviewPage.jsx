/**
 * /review — NuRO向け 事前レビュー画面
 *
 * 3カラム構成:
 *   左: 未レビューセッション一覧
 *   中: 様式プレビュー（指摘セルをティール/赤/黄ハイライト）
 *   右: AI指摘パネル + AIチャット
 *
 * カラーテーマ: ティール系グリーン（NuRO既存サイトに準拠）
 */
import { useState, useRef, useEffect } from "react";
import { AppHeader } from "../App.jsx";

const API_BASE = "http://localhost:8000/api";

// ── カラー定義（ティール基調） ─────────────────────────────────────────────
const T = {
  bg:          "#0f1117",
  surface:     "#1a1d27",
  surfaceHover:"#22263a",
  border:      "#2a2e42",
  borderLight: "#363b55",

  // ティール（NuROプライマリ）
  teal:        "#0d9488",
  tealLight:   "#14b8a6",
  tealSoft:    "rgba(13,148,136,0.12)",
  tealGlow:    "rgba(13,148,136,0.25)",

  text:        "#e2e8f0",
  textMuted:   "#8892a4",
  textDim:     "#4a5568",

  // 指摘 severity 色
  yellow:      "#f59e0b",
  yellowSoft:  "rgba(245,158,11,0.15)",
  red:         "#ef4444",
  redSoft:     "rgba(239,68,68,0.15)",
  green:       "#10b981",
  greenSoft:   "rgba(16,185,129,0.15)",
};

// ── ユーティリティ ──────────────────────────────────────────────────────────
function colIdxToLetter(n) {
  let s = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function severityColor(severity) {
  if (severity === "要確認") return { bg: T.yellowSoft, border: T.yellow, text: T.yellow };
  return { bg: T.redSoft,    border: T.red,    text: T.red };
}

function Spinner({ size = 14, color = T.teal }) {
  return (
    <div style={{
      width: size, height: size,
      border: `2px solid rgba(255,255,255,0.2)`,
      borderTop: `2px solid ${color}`,
      borderRadius: "50%",
      animation: "spin 0.8s linear infinite",
      flexShrink: 0,
    }} />
  );
}

// ── 左パネル: セッション一覧（未レビュー/レビュー済みタブ切替） ─────────────
function SessionSidebar({ sessions, selectedSession, isLoadingSessions, onSelect, onStartReview, onSaveReview, onUploadForm, isReviewing, isUploading, isSaving, hasReviewItems, activeTab, onTabChange }) {
  const fileInputRef = useRef(null);
  const unreviewed = sessions.filter(s => s.review_status !== "completed");
  const reviewed   = sessions.filter(s => s.review_status === "completed");
  const isSelectedUnreviewed = selectedSession && selectedSession.review_status !== "completed";
  const isSelectedCompleted  = selectedSession && selectedSession.review_status === "completed";
  const displayList = activeTab === "reviewed" ? reviewed : unreviewed;

  const SessionItem = ({ s }) => {
    const isSelected = selectedSession?.session_id === s.session_id;
    const isCompleted = s.review_status === "completed";
    const displayName = s.session_name || s.utility_name || "無題のセッション";
    return (
      <div
        onClick={() => onSelect(s)}
        style={{
          padding: "10px 16px",
          borderBottom: `1px solid ${T.border}`,
          cursor: "pointer",
          background: isSelected ? T.tealSoft : "transparent",
          borderLeft: isSelected ? `3px solid ${T.teal}` : "3px solid transparent",
          transition: "all 0.15s",
        }}
        onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = T.surfaceHover; }}
        onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
      >
        <div style={{ fontSize: 12, fontWeight: 600, color: isSelected ? T.tealLight : T.text, marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {displayName}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 10, color: T.textDim }}>
            {s.created_at ? new Date(s.created_at).toLocaleDateString("ja-JP", { month: "2-digit", day: "2-digit" }) : ""}
          </span>
          {s.review_status === "in_progress" && s.progress && s.progress.total > 0 && (
            <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 8, background: "rgba(245,158,11,0.12)", border: "1px solid rgba(245,158,11,0.35)", color: T.yellow }}>
              {s.progress.decided}/{s.progress.total}件判定済
            </span>
          )}
          {isCompleted && (
            <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 8, background: T.greenSoft, border: `1px solid ${T.green}60`, color: T.green }}>
              完了
            </span>
          )}
        </div>
      </div>
    );
  };

  return (
    <div style={{ width: 260, flexShrink: 0, borderRight: `1px solid ${T.border}`, display: "flex", flexDirection: "column", background: T.surface, overflow: "hidden" }}>
      {/* タブ */}
      <div style={{ display: "flex", borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
        {[
          { key: "unreviewed", label: "未レビュー", count: unreviewed.length },
          { key: "reviewed",   label: "レビュー済み", count: reviewed.length },
        ].map(({ key, label, count }) => (
          <button
            key={key}
            onClick={() => onTabChange(key)}
            style={{
              flex: 1, padding: "10px 4px",
              border: "none",
              borderBottom: `2px solid ${activeTab === key ? T.teal : "transparent"}`,
              background: "transparent",
              color: activeTab === key ? T.tealLight : T.textMuted,
              fontSize: 11, fontWeight: activeTab === key ? 700 : 400,
              cursor: "pointer", transition: "all 0.15s", fontFamily: "inherit",
              display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
            }}
          >
            {label}
            <span style={{
              fontSize: 10, padding: "0px 6px", borderRadius: 10,
              background: activeTab === key ? T.teal : T.border,
              color: activeTab === key ? "#fff" : T.textMuted,
            }}>{count}</span>
          </button>
        ))}
      </div>

      {/* セッションリスト */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {isLoadingSessions ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 24, gap: 8 }}>
            <Spinner /> <span style={{ fontSize: 12, color: T.textMuted }}>取得中...</span>
          </div>
        ) : displayList.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", fontSize: 12, color: T.textDim }}>なし</div>
        ) : (
          displayList.map(s => <SessionItem key={s.session_id} s={s} />)
        )}
      </div>

      {/* ボタンエリア */}
      <div style={{ padding: "10px 12px", borderTop: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 8 }}>
        {/* 様式Excel（転記結果）をアップロードしてレビュー */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xls"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUploadForm(f);
            e.target.value = "";
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
          title="完成した様式（転記結果Excel）を読み込んでレビュー対象に追加します"
          style={{
            width: "100%", padding: "8px", borderRadius: 8,
            border: `1px dashed ${T.teal}`,
            background: isUploading ? T.borderLight : "transparent",
            color: isUploading ? T.textDim : T.tealLight,
            fontSize: 12, fontWeight: 700,
            cursor: isUploading ? "not-allowed" : "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            gap: 6, transition: "all 0.15s", fontFamily: "inherit",
          }}
        >
          {isUploading ? <><Spinner color={T.teal} size={12} />読み込み中...</> : "＋ 様式Excelを追加"}
        </button>
        {/* 未レビュー: レビュー結果を保存 */}
        {isSelectedUnreviewed && hasReviewItems && (
          <button
            onClick={onSaveReview}
            disabled={isSaving}
            style={{
              width: "100%", padding: "8px", borderRadius: 8, border: `1px solid ${T.green}`,
              background: isSaving ? T.borderLight : T.greenSoft,
              color: isSaving ? T.textDim : T.green,
              fontSize: 12, fontWeight: 700,
              cursor: isSaving ? "not-allowed" : "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
              gap: 6, transition: "all 0.15s", fontFamily: "inherit",
            }}
          >
            {isSaving ? <><Spinner color={T.green} size={12} />保存中...</> : "✓ レビュー結果を保存"}
          </button>
        )}
        {/* レビュー済み: 上書き保存 */}
        {isSelectedCompleted && hasReviewItems && (
          <button
            onClick={onSaveReview}
            disabled={isSaving}
            style={{
              width: "100%", padding: "8px", borderRadius: 8,
              border: `1px solid ${T.teal}`,
              background: isSaving ? T.borderLight : T.tealSoft,
              color: isSaving ? T.textDim : T.tealLight,
              fontSize: 12, fontWeight: 700,
              cursor: isSaving ? "not-allowed" : "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
              gap: 6, transition: "all 0.15s", fontFamily: "inherit",
            }}
          >
            {isSaving ? <><Spinner color={T.teal} size={12} />保存中...</> : "↑ 上書き保存"}
          </button>
        )}
        <button
          onClick={onStartReview}
          disabled={!selectedSession || isReviewing}
          style={{
            width: "100%", padding: "10px", borderRadius: 8, border: "none",
            background: !selectedSession || isReviewing
              ? T.borderLight
              : `linear-gradient(135deg, ${T.teal}, ${T.tealLight})`,
            color: !selectedSession || isReviewing ? T.textDim : "#fff",
            fontSize: 13, fontWeight: 700,
            cursor: !selectedSession || isReviewing ? "not-allowed" : "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            gap: 8, transition: "all 0.15s", fontFamily: "inherit",
          }}
        >
          {isReviewing ? <><Spinner color="#fff" />AIレビュー中...</> : "▶ レビュー開始"}
        </button>
      </div>
    </div>
  );
}

// ── 中央パネル: 様式プレビュー ────────────────────────────────────────────
function ReviewGridView({ template, templateError, mappings, reviewItems, selectedCell, onCellClick, feedbackMap }) {
  const [hoveredAddr, setHoveredAddr] = useState(null);

  if (templateError) {
    return <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: T.red, fontSize: 13 }}>テンプレート読込エラー: {templateError}</div>;
  }
  if (!template) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 10, color: T.textDim }}>
        <Spinner /> <span style={{ fontSize: 13 }}>様式を読み込み中...</span>
      </div>
    );
  }

  // セルアドレス → mapping / review item マップを構築
  const mappingMap = {};
  (mappings || []).forEach((m) => { mappingMap[m.cell_address] = m; });

  const reviewMap = {};
  (reviewItems || []).forEach((item) => {
    if (item.cell_address) reviewMap[item.cell_address] = item;
  });

  const { max_row, max_col, cells, merged_cells, col_widths, row_heights } = template;

  // 2Dグリッド構築
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
      {/* 凡例 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 8, fontSize: 11, color: T.textMuted, alignItems: "center", flexWrap: "wrap" }}>
        <LegendDot color={T.red}    label="AIからの指摘" />
        <LegendDot color={T.yellow} label="要確認" />
        <LegendDot color={T.green}  label="確認済み（承諾/棄却後）" />
        <LegendDot color={T.teal}   label="転記済みセル" />
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", tableLayout: "fixed", fontSize: 11, minWidth: "max-content" }}>
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
                  const reviewItem = reviewMap[cell.address];
                  const isSelected = selectedCell?.cell_address === cell.address;
                  const isHovered = hoveredAddr === cell.address;
                  const feedbackStatus = reviewItem ? feedbackMap[reviewItem.item_id] : null;
                  const isClickable = !!(mapping || reviewItem);

                  let bg = "transparent", textColor = T.textMuted, borderStyle = `1px solid ${T.border}`;
                  if (feedbackStatus === "accepted") {
                    bg = T.greenSoft; textColor = T.green; borderStyle = `1px solid ${T.green}`;
                  } else if (feedbackStatus === "rejected") {
                    bg = "rgba(100,100,100,0.08)"; textColor = T.textDim; borderStyle = `1px solid ${T.borderLight}`;
                  } else if (reviewItem) {
                    const sc = severityColor(reviewItem.severity);
                    if (isSelected) { bg = sc.bg; textColor = sc.text; borderStyle = `2px solid ${sc.border}`; }
                    else if (isHovered) { bg = sc.bg; textColor = sc.text; borderStyle = `1px solid ${sc.border}`; }
                    else { bg = `${sc.bg.replace("0.15", "0.08")}`; textColor = sc.text; borderStyle = `1px solid ${sc.border}60`; }
                  } else if (mapping) {
                    if (isSelected) { bg = T.tealSoft; textColor = T.tealLight; borderStyle = `2px solid ${T.teal}`; }
                    else if (isHovered) { bg = T.tealSoft; textColor = T.tealLight; borderStyle = `1px solid ${T.teal}`; }
                    else { bg = "rgba(13,148,136,0.06)"; textColor = T.tealLight; borderStyle = `1px solid rgba(13,148,136,0.3)`; }
                  }

                  return (
                    <td
                      key={cIdx}
                      rowSpan={cell.rowspan}
                      colSpan={cell.colspan}
                      onClick={() => isClickable && onCellClick(mapping || { cell_address: cell.address, field_name: reviewItem?.field_name, value: "", reasoning: "" }, reviewItem)}
                      onMouseEnter={() => isClickable && setHoveredAddr(cell.address)}
                      onMouseLeave={() => setHoveredAddr(null)}
                      title={reviewItem ? `${reviewItem.severity}: ${reviewItem.comment}` : (mapping ? `${mapping.field_name}: ${mapping.value}` : undefined)}
                      style={{
                        border: borderStyle, background: bg,
                        cursor: isClickable ? "pointer" : "default",
                        padding: "2px 5px", color: textColor,
                        fontWeight: (mapping || reviewItem) ? 600 : 400,
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

function LegendDot({ color, label }) {
  return (
    <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
      <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 2, background: `${color}30`, border: `1px solid ${color}` }} />
      {label}
    </span>
  );
}

// ── 下部ドロワー: 選択セルの指摘詳細 ────────────────────────────────────────
function ReviewItemDrawer({ selectedReviewItem, onAccept, onReject, onUndo, feedbackMap }) {
  if (!selectedReviewItem) return null;

  const status = feedbackMap[selectedReviewItem.item_id];
  const scColor = severityColor(selectedReviewItem.severity);

  return (
    <div style={{
      borderTop: `1px solid ${T.border}`,
      background: T.surface,
      padding: "14px 18px",
      flexShrink: 0,
      maxHeight: 200,
      overflow: "auto",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: T.textMuted }}>
          選択中の指摘
        </span>
        <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, background: scColor.bg, border: `1px solid ${scColor.border}`, color: scColor.text }}>
          {selectedReviewItem.severity}
        </span>
        <span style={{ fontSize: 11, color: T.textDim, fontFamily: "monospace" }}>{selectedReviewItem.cell_address}</span>
        {status && (
          <span style={{
            fontSize: 11, padding: "2px 8px", borderRadius: 4,
            background: status === "accepted" ? T.greenSoft : "rgba(100,100,100,0.12)",
            border: `1px solid ${status === "accepted" ? T.green : T.borderLight}`,
            color: status === "accepted" ? T.green : T.textDim,
          }}>
            {status === "accepted" ? "承諾済み" : "棄却済み"}
          </span>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 4 }}>指摘内容</div>
          <div style={{ fontSize: 13, color: T.text, lineHeight: 1.6 }}>{selectedReviewItem.comment}</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 4 }}>根拠（{selectedReviewItem.knowledge_source}）</div>
          <div style={{ fontSize: 12, color: T.textMuted, lineHeight: 1.6 }}>{selectedReviewItem.evidence}</div>
        </div>
      </div>
      {!status ? (
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <ActionBtn label="承諾" color={T.green} onClick={() => onAccept(selectedReviewItem.item_id)} />
          <ActionBtn label="棄却" color={T.red}   onClick={() => onReject(selectedReviewItem.item_id)} />
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <UndoBtn onClick={() => onUndo(selectedReviewItem.item_id)} />
        </div>
      )}
    </div>
  );
}

function ActionBtn({ label, color, onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        padding: "6px 18px", borderRadius: 6,
        border: `1px solid ${color}`,
        background: hover ? `${color}22` : "transparent",
        color: color, fontSize: 12, fontWeight: 700,
        cursor: "pointer", transition: "all 0.15s", fontFamily: "inherit",
      }}
    >
      {label}
    </button>
  );
}

// ── 右パネル: AI指摘一覧 + チャット ──────────────────────────────────────
function ReviewPanel({ reviewItems, summary, onAccept, onReject, onUndo, feedbackMap, selectedReviewItem, onSelectItem, sessionId, reviewId }) {
  const [tab, setTab] = useState("items");
  const [messages, setMessages] = useState([
    { role: "ai", text: "AIレビュー担当です。指摘内容について質問があればどうぞ。" },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim()) return;
    const msg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: msg }]);
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          cell_address: selectedReviewItem?.cell_address || "",
          field_name: selectedReviewItem?.field_name || "",
          field_value: "",
          reasoning: selectedReviewItem?.evidence || "",
        }),
      });
      const data = await res.json();
      setMessages((prev) => [...prev, { role: "ai", text: data.answer }]);
    } catch {
      setMessages((prev) => [...prev, { role: "ai", text: "エラーが発生しました。" }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ width: 320, flexShrink: 0, borderLeft: `1px solid ${T.border}`, display: "flex", flexDirection: "column", background: T.surface, overflow: "hidden" }}>
      {/* タブ切替 */}
      <div style={{ display: "flex", borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
        {[{ key: "items", label: "AI指摘一覧" }, { key: "chat", label: "AIチャット" }].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            style={{
              flex: 1, padding: "11px 0",
              border: "none", borderBottom: `2px solid ${tab === key ? T.teal : "transparent"}`,
              background: "transparent", color: tab === key ? T.tealLight : T.textMuted,
              fontSize: 12, fontWeight: tab === key ? 700 : 400,
              cursor: "pointer", transition: "all 0.15s", fontFamily: "inherit",
            }}
          >
            {label}
            {key === "items" && reviewItems.length > 0 && (
              <span style={{ marginLeft: 6, fontSize: 10, padding: "1px 6px", borderRadius: 10, background: T.tealSoft, color: T.tealLight }}>
                {reviewItems.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "items" ? (
        <>
          <ReviewItemsList
            reviewItems={reviewItems}
            summary={summary}
            onAccept={onAccept}
            onReject={onReject}
            onUndo={onUndo}
            feedbackMap={feedbackMap}
            selectedReviewItem={selectedReviewItem}
            onSelectItem={onSelectItem}
          />
        </>
      ) : (
        <ChatTab
          messages={messages}
          input={input}
          isLoading={isLoading}
          setInput={setInput}
          onSend={handleSend}
          selectedReviewItem={selectedReviewItem}
          bottomRef={bottomRef}
        />
      )}
    </div>
  );
}

function ReviewItemsList({ reviewItems, summary, onAccept, onReject, onUndo, feedbackMap, selectedReviewItem, onSelectItem }) {
  return (
    <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
      {summary && (
        <div style={{ padding: "10px 14px", borderBottom: `1px solid ${T.border}`, fontSize: 12, color: T.textMuted, lineHeight: 1.6 }}>
          {summary}
        </div>
      )}
      {reviewItems.length === 0 ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 12, color: T.textDim }}>
          <div style={{ fontSize: 32, opacity: 0.3 }}>✓</div>
          <div style={{ fontSize: 13 }}>指摘事項はありません</div>
        </div>
      ) : (
        reviewItems.map((item) => {
          const scColor = severityColor(item.severity);
          const status = feedbackMap[item.item_id];
          const isSelected = selectedReviewItem?.item_id === item.item_id;

          let cardBg = isSelected ? scColor.bg : "transparent";
          let cardBorder = isSelected ? `1px solid ${scColor.border}` : `1px solid ${T.border}`;
          if (status === "accepted") { cardBg = T.greenSoft; cardBorder = `1px solid ${T.green}60`; }
          else if (status === "rejected") { cardBg = "rgba(100,100,100,0.06)"; cardBorder = `1px solid ${T.borderLight}`; }

          return (
            <div
              key={item.item_id}
              onClick={() => onSelectItem(item)}
              style={{
                padding: "12px 14px",
                borderBottom: `1px solid ${T.border}`,
                cursor: "pointer",
                background: cardBg,
                borderLeft: isSelected ? `3px solid ${scColor.border}` : "3px solid transparent",
                transition: "all 0.15s",
              }}
              onMouseEnter={(e) => { if (!isSelected && !status) e.currentTarget.style.background = T.surfaceHover; }}
              onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = status === "accepted" ? T.greenSoft : status === "rejected" ? "rgba(100,100,100,0.06)" : "transparent"; }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                <span style={{
                  fontSize: 10, padding: "1px 7px", borderRadius: 10,
                  background: status === "accepted" ? T.greenSoft : status === "rejected" ? "rgba(100,100,100,0.12)" : scColor.bg,
                  border: `1px solid ${status === "accepted" ? T.green : status === "rejected" ? T.borderLight : scColor.border}`,
                  color: status === "accepted" ? T.green : status === "rejected" ? T.textDim : scColor.text,
                }}>
                  {status ? (status === "accepted" ? "承諾" : "棄却") : item.severity}
                </span>
                <span style={{ fontSize: 11, color: T.textDim, fontFamily: "monospace" }}>{item.cell_address}</span>
                <span style={{ fontSize: 11, color: T.textMuted, marginLeft: "auto" }}>{item.field_name}</span>
              </div>
              <div style={{ fontSize: 12, color: T.text, lineHeight: 1.6, marginBottom: 8 }}>{item.comment}</div>
              {!status ? (
                <div style={{ display: "flex", gap: 6 }}>
                  <SmallBtn label="承諾" color={T.green} onClick={(e) => { e.stopPropagation(); onAccept(item.item_id); }} />
                  <SmallBtn label="棄却" color={T.red}   onClick={(e) => { e.stopPropagation(); onReject(item.item_id); }} />
                </div>
              ) : (
                <UndoBtn onClick={(e) => { e.stopPropagation(); onUndo(item.item_id); }} />
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

function SmallBtn({ label, color, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "3px 12px", borderRadius: 4,
        border: `1px solid ${color}40`,
        background: `${color}18`, color: color,
        fontSize: 11, fontWeight: 700,
        cursor: "pointer", fontFamily: "inherit",
      }}
    >
      {label}
    </button>
  );
}

function UndoBtn({ onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title="承諾/棄却を取り消して未決定に戻す"
      style={{
        padding: "3px 10px", borderRadius: 4,
        border: `1px solid ${T.borderLight}`,
        background: hover ? T.surfaceHover : "transparent",
        color: T.textDim, fontSize: 10, fontWeight: 600,
        cursor: "pointer", fontFamily: "inherit",
        transition: "all 0.15s",
      }}
    >
      ↩ 取り消し
    </button>
  );
}

function ChatTab({ messages, input, isLoading, setInput, onSend, selectedReviewItem, bottomRef }) {
  return (
    <>
      {selectedReviewItem && (
        <div style={{ padding: "8px 14px", borderBottom: `1px solid ${T.border}`, background: T.tealSoft, fontSize: 11, color: T.tealLight }}>
          選択中の指摘: {selectedReviewItem.field_name}（{selectedReviewItem.cell_address}）
        </div>
      )}
      <div style={{ flex: 1, overflow: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", flexDirection: m.role === "user" ? "row-reverse" : "row", gap: 8, alignItems: "flex-start" }}>
            <div style={{ width: 26, height: 26, borderRadius: "50%", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, background: m.role === "user" ? "#1e3a5f" : T.tealSoft, border: `1px solid ${m.role === "user" ? "#2a4a7f" : T.teal}40` }}>
              {m.role === "user" ? "👤" : "🤖"}
            </div>
            <div style={{ maxWidth: "82%", padding: "9px 12px", borderRadius: m.role === "user" ? "12px 4px 12px 12px" : "4px 12px 12px 12px", background: m.role === "user" ? "#1e3a5f" : T.surface, border: `1px solid ${m.role === "user" ? "#2a4a7f" : T.border}`, fontSize: 12, lineHeight: 1.7, color: "#e2e8f0", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {m.text}
            </div>
          </div>
        ))}
        {isLoading && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ width: 26, height: 26, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, background: T.tealSoft }}>🤖</div>
            <div style={{ padding: "9px 12px", borderRadius: "4px 12px 12px 12px", background: T.surface, border: `1px solid ${T.border}`, fontSize: 12, color: T.textMuted }}>考えています...</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div style={{ padding: "10px 14px", borderTop: `1px solid ${T.border}`, display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && onSend()}
          placeholder="指摘内容について質問する..."
          style={{ flex: 1, padding: "8px 12px", borderRadius: 7, border: `1px solid ${T.borderLight}`, background: T.bg, color: T.text, fontSize: 12, outline: "none", fontFamily: "inherit" }}
        />
        <button
          onClick={onSend}
          disabled={!input.trim() || isLoading}
          style={{ padding: "8px 14px", borderRadius: 7, border: "none", background: !input.trim() ? T.borderLight : T.teal, color: !input.trim() ? T.textDim : "#fff", fontWeight: 700, fontSize: 12, cursor: !input.trim() ? "not-allowed" : "pointer", transition: "all 0.15s", fontFamily: "inherit" }}
        >
          送信
        </button>
      </div>
    </>
  );
}

// ── メインページ ───────────────────────────────────────────────────────────
export default function ReviewPage() {
  const [sessions, setSessions] = useState([]);
  const [isLoadingSessions, setIsLoadingSessions] = useState(true);
  const [selectedSession, setSelectedSession] = useState(null);
  const [activeTab, setActiveTab] = useState("unreviewed");
  const [isReviewing, setIsReviewing] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [reviewId, setReviewId] = useState(null);
  const [reviewItems, setReviewItems] = useState([]);
  const [sessionMappings, setSessionMappings] = useState([]);
  const [summary, setSummary] = useState("");
  const [feedbackMap, setFeedbackMap] = useState({});
  const [selectedCell, setSelectedCell] = useState(null);
  const [selectedReviewItem, setSelectedReviewItem] = useState(null);
  const [template, setTemplate] = useState(null);
  const [templateError, setTemplateError] = useState(null);
  const [error, setError] = useState("");
  const [retrievalTrace, setRetrievalTrace] = useState([]);

  // セッション一覧取得（新エンドポイント: review_status + progress 付き）
  const fetchSessions = () => {
    setIsLoadingSessions(true);
    return fetch(`${API_BASE}/sessions`)
      .then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then((data) => { setSessions(data); setIsLoadingSessions(false); return data; })
      .catch((e) => { setError(String(e)); setIsLoadingSessions(false); return []; });
  };

  // 最新レビュー結果を復元する共通処理
  const _restoreReviewResult = (sessionId, frameName, sheetName) => {
    fetch(`${API_BASE}/review/${sessionId}/result`)
      .then((r) => r.ok ? r.json() : null)
      .then((result) => {
        if (!result) return;
        setReviewId(result.review_id);
        setReviewItems(result.review_items || []);
        setSessionMappings(result.mappings || []);
        setSummary(result.summary || "");

        // feedbackMap を feedbacks の decision フィールドから正確に復元する
        const restoredMap = {};
        for (const f of result.feedbacks || []) {
          restoredMap[f.item_id] = f.decision === "accept" ? "accepted" : "rejected";
        }
        setFeedbackMap(restoredMap);

        fetch(`${API_BASE}/result-layout/${sessionId}?frame_name=${frameName || "frameB"}&sheet_name=${sheetName || "MRC1"}`)
          .then((r) => r.ok ? r.json() : null)
          .then((layout) => { if (layout) setTemplate(layout); })
          .catch(() => {});
      })
      .catch(() => {});
  };

  // 初回マウント: セッション取得 + 前回セッション復元
  useEffect(() => {
    fetchSessions().then((data) => {
      const savedId = sessionStorage.getItem("nuro_last_session_id");
      if (!savedId || data.length === 0) return;
      const saved = data.find((s) => s.session_id === savedId);
      if (saved) {
        _selectSession(saved);
        if (saved.review_status !== "not_reviewed") {
          _restoreReviewResult(savedId, saved.frame_name, saved.sheet_name);
        }
        if (saved.review_status === "completed") setActiveTab("reviewed");
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // セッション選択の共通処理（内部用）
  const _selectSession = (session) => {
    setSelectedSession(session);
    setReviewItems([]);
    setSessionMappings([]);
    setSummary("");
    setFeedbackMap({});
    setSelectedCell(null);
    setSelectedReviewItem(null);
    setTemplate(null);
    setTemplateError(null);

    const sheet = session.sheet_name || "MRC1";
    fetch(`${API_BASE}/template?sheet_name=${sheet}`)
      .then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(setTemplate)
      .catch((e) => setTemplateError(String(e)));
  };

  // セッション選択時: sessionStorageに保存、レビュー済みなら結果を自動復元
  const handleSelectSession = (session) => {
    sessionStorage.setItem("nuro_last_session_id", session.session_id);
    _selectSession(session);
    if (session.review_status !== "not_reviewed") {
      _restoreReviewResult(session.session_id, session.frame_name, session.sheet_name);
    }
  };

  // AIレビュー実行
  const handleStartReview = async () => {
    if (!selectedSession) return;
    setIsReviewing(true);
    setError("");
    setReviewItems([]);
    setSummary("");
    setFeedbackMap({});
    setRetrievalTrace([]);

    try {
      const res = await fetch(`${API_BASE}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: selectedSession.session_id,
          utility_name: selectedSession.utility_name,
          sheet_name: selectedSession.sheet_name || "MRC1",
          frame_name: selectedSession.frame_name || "frameB",
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "レビューに失敗しました");
      }
      const data = await res.json();
      setReviewId(data.review_id);
      setReviewItems(data.review_items || []);
      setSessionMappings(data.mappings || []);
      setSummary(data.summary || "");
      setRetrievalTrace(data.retrieval_trace || []);

      // 転記済み様式レイアウトを取得（指摘セルの実際の値を表示するため）
      fetch(`${API_BASE}/result-layout/${selectedSession.session_id}?frame_name=${selectedSession.frame_name || "frameB"}&sheet_name=${selectedSession.sheet_name || "MRC1"}`)
        .then((r) => r.ok ? r.json() : null)
        .then((layout) => { if (layout) setTemplate(layout); })
        .catch(() => {});

    } catch (e) {
      setError(e.message);
    } finally {
      setIsReviewing(false);
    }
  };

  // 様式Excel（転記結果）をアップロードしてレビュー対象セッションを作成する
  const handleUploadForm = async (file) => {
    if (!file) return;
    setIsUploading(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("frame_name", "frameB");
      fd.append("sheet_name", "MRC1");
      const res = await fetch(`${API_BASE}/review/upload`, { method: "POST", body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "様式の読み込みに失敗しました");
      }
      const data = await res.json();

      // セッション一覧を更新し、作成したセッションを選択状態にする
      const list = await fetchSessions();
      const created = list.find((s) => s.session_id === data.session_id) || {
        session_id: data.session_id,
        utility_name: "",
        frame_name: data.frame_name,
        sheet_name: data.sheet_name,
        review_status: "not_reviewed",
      };
      sessionStorage.setItem("nuro_last_session_id", created.session_id);
      setActiveTab("unreviewed");

      // _selectSession は空テンプレートを読むため、ここでは選択状態のリセットのみ行い
      // アップロードした様式の実値（result-layout）を中央プレビューに表示する
      setSelectedSession(created);
      setReviewItems([]);
      setSummary("");
      setFeedbackMap({});
      setSelectedCell(null);
      setSelectedReviewItem(null);
      setTemplateError(null);
      setSessionMappings(data.mappings || []);

      fetch(`${API_BASE}/result-layout/${data.session_id}?frame_name=${data.frame_name}&sheet_name=${data.sheet_name}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((layout) => { if (layout) setTemplate(layout); })
        .catch(() => {});
    } catch (e) {
      setError(e.message);
    } finally {
      setIsUploading(false);
    }
  };

  // セルクリック
  const handleCellClick = (mapping, reviewItem) => {
    setSelectedCell(mapping);
    if (reviewItem) setSelectedReviewItem(reviewItem);
  };

  // 右パネルで指摘を選択
  const handleSelectReviewItem = (item) => {
    setSelectedReviewItem(item);
    if (item.cell_address) setSelectedCell({ cell_address: item.cell_address, field_name: item.field_name, value: "", reasoning: "" });
  };

  // 承諾/棄却
  const submitFeedback = async (itemId, decision) => {
    const newFeedbackMap = { ...feedbackMap, [itemId]: decision === "accept" ? "accepted" : "rejected" };
    setFeedbackMap(newFeedbackMap);
    if (!reviewId) return;
    try {
      await fetch(`${API_BASE}/review/${reviewId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: itemId, decision, comment: "", session_id: selectedSession?.session_id || "" }),
      });
      // 全指摘が判定済みになったらセッション一覧を更新してステータスを反映する
      if (reviewItems.length > 0 && reviewItems.every(item => newFeedbackMap[item.item_id])) {
        fetchSessions();
      }
    } catch {
      // フロント側は楽観的更新済み。エラーはログのみ
    }
  };

  // レビュー結果を保存（未レビュー→レビュー済み移動 / 上書き保存）
  const handleSaveReview = async () => {
    if (!selectedSession) return;
    setIsSaving(true);
    setError("");
    try {
      // Step1: 現在の feedbackMap を Firestore に一括同期（リアルタイム保存の補完）
      if (reviewId && Object.keys(feedbackMap).length > 0) {
        const feedbacksToSync = Object.entries(feedbackMap).map(([itemId, status]) => ({
          item_id: itemId,
          decision: status === "accepted" ? "accept" : "reject",
        }));
        const syncRes = await fetch(`${API_BASE}/review/${reviewId}/feedbacks/sync`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ feedbacks: feedbacksToSync, session_id: selectedSession?.session_id || "" }),
        });
        if (!syncRes.ok) throw new Error("フィードバックの保存に失敗しました");
      }
      // Step2: セッションを完了済みにマーク
      await fetch(`${API_BASE}/sessions/${selectedSession.session_id}/complete`, {
        method: "PATCH",
      });
      await fetchSessions();
      setActiveTab("reviewed");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setIsSaving(false);
    }
  };

  // 承諾/棄却の取り消し
  const handleUndo = async (itemId) => {
    setFeedbackMap((prev) => {
      const next = { ...prev };
      delete next[itemId];
      return next;
    });
    if (!reviewId) return;
    try {
      await fetch(`${API_BASE}/review/${reviewId}/feedback/${itemId}?session_id=${selectedSession?.session_id || ""}`, {
        method: "DELETE",
      });
      // completed → in_progress に戻る可能性があるためセッション一覧を更新する
      fetchSessions();
    } catch {
      // 楽観的更新済み。エラーはログのみ
    }
  };


  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: "'Noto Sans JP', 'Courier New', monospace", color: T.text, overflow: "hidden" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&display=swap');
        @keyframes spin { to { transform: rotate(360deg); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2e42; border-radius: 3px; }
      `}</style>

      <AppHeader rightSlot={
        error
          ? <span style={{ fontSize: 12, color: T.red }}>✗ {error}</span>
          : reviewItems.length > 0
            ? <span style={{ fontSize: 12, color: T.tealLight }}>✓ {reviewItems.length}件の指摘を検出</span>
            : null
      } />

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* 左: セッション一覧 */}
        <SessionSidebar
          sessions={sessions}
          selectedSession={selectedSession}
          isLoadingSessions={isLoadingSessions}
          onSelect={handleSelectSession}
          onStartReview={handleStartReview}
          onSaveReview={handleSaveReview}
          onUploadForm={handleUploadForm}
          isReviewing={isReviewing}
          isUploading={isUploading}
          isSaving={isSaving}
          hasReviewItems={reviewItems.length > 0}
          activeTab={activeTab}
          onTabChange={setActiveTab}
        />

        {/* 中: 様式プレビュー + 下部ドロワー */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ padding: "10px 16px", borderBottom: `1px solid ${T.border}`, background: T.surface, fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: T.textMuted, display: "flex", alignItems: "center", gap: 12 }}>
            <span>様式プレビュー</span>
            {selectedSession && (
              <span style={{ fontSize: 12, color: T.tealLight, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
                {selectedSession.utility_name} — {selectedSession.sheet_name}
              </span>
            )}
          </div>

          {selectedSession ? (
            <ReviewGridView
              template={template}
              templateError={templateError}
              mappings={sessionMappings}
              reviewItems={reviewItems}
              selectedCell={selectedCell}
              onCellClick={handleCellClick}
              feedbackMap={feedbackMap}
            />
          ) : (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 16, color: T.textDim }}>
              <div style={{ fontSize: 40, opacity: 0.2 }}>📋</div>
              <div style={{ fontSize: 14 }}>左のリストからセッションを選択してください</div>
              <div style={{ fontSize: 12 }}>選択後「レビュー開始」でAIレビューを実行します</div>
            </div>
          )}

          {/* 指摘詳細ドロワー */}
          <ReviewItemDrawer
            selectedReviewItem={selectedReviewItem}
            onAccept={(id) => submitFeedback(id, "accept")}
            onReject={(id) => submitFeedback(id, "reject")}
            onUndo={handleUndo}
            feedbackMap={feedbackMap}
          />
        </div>

        {/* 右: AI指摘パネル + チャット */}
        <ReviewPanel
          reviewItems={reviewItems}
          summary={summary}
          onAccept={(id) => submitFeedback(id, "accept")}
          onReject={(id) => submitFeedback(id, "reject")}
          onUndo={handleUndo}
          feedbackMap={feedbackMap}
          selectedReviewItem={selectedReviewItem}
          onSelectItem={handleSelectReviewItem}
          sessionId={selectedSession?.session_id}
          reviewId={reviewId}
        />
      </div>
    </div>
  );
}
