/**
 * /inquiry/tickets — 問い合わせ一覧（フェーズ2・DESIGN §2）
 *
 * ロールで見え方が変わる（D-16・PoC簡易運用）：
 *   - 電力ユーザー：自分（表示名）が起票した問い合わせのみ（?requester=）
 *   - NuRO担当者：全件（未回答一覧の確認 → 詳細で回答登録・§1-1 S5）
 * 並びは updated_at 降順（動きのあった問い合わせが先頭・API側で保証）。
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listInquiries } from "./api.js";
import {
  C, ErrorCard, InquiryShell, Spinner, StatusBadge, formatTimestamp, useIdentity,
} from "./shared.jsx";

export default function InquiryListPage() {
  const navigate = useNavigate();
  const [identity, updateIdentity] = useIdentity();
  const [inquiries, setInquiries] = useState(null); // null=読込中
  const [error, setError] = useState(null);

  const isDenryoku = identity.role === "denryoku";
  const requesterFilter = isDenryoku ? identity.displayName.trim() : null;
  // 電力ロールで表示名が空のまま取得すると「全件」が自分の分として見えてしまうため
  // （requester 未指定=NuRO向け全件・§4-1）、入力されるまで取得しない
  const needsDisplayName = isDenryoku && !requesterFilter;

  useEffect(() => {
    if (needsDisplayName) return;
    let cancelled = false;
    setInquiries(null);
    setError(null);
    listInquiries(requesterFilter)
      .then((list) => { if (!cancelled) setInquiries(list); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [requesterFilter, needsDisplayName]);

  const openCount = inquiries?.filter((inq) => inq.status === "open").length ?? 0;

  return (
    <InquiryShell
      rightSlot="問い合わせ一覧 — 起票からNuRO回答・解決までを管理"
      identity={identity}
      onIdentityChange={updateIdentity}
      maxWidth="960px"
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "13px", fontWeight: 700 }}>
          {isDenryoku ? `自分の問い合わせ（${identity.displayName || "表示名未設定"}）` : "全問い合わせ（NuRO担当者ビュー）"}
        </span>
        {inquiries && (
          <span style={{ fontSize: "11px", color: C.textMuted }}>
            {inquiries.length}件{!isDenryoku && openCount > 0 && `・未回答 ${openCount}件`}
          </span>
        )}
      </div>

      {error && <ErrorCard message={error} />}

      {needsDisplayName && (
        <div style={{
          padding: "16px", borderRadius: "10px",
          background: C.warningSoft, border: `1px solid ${C.warning}`,
          fontSize: "12.5px", color: C.warning, lineHeight: 1.8,
        }}>
          右上の「表示名」を入力すると、自分が起票した問い合わせが表示されます。
        </div>
      )}

      {!error && !needsDisplayName && inquiries === null && (
        <div style={{ display: "flex", alignItems: "center", gap: "10px", color: C.textMuted, fontSize: "12px" }}>
          <Spinner />読み込み中...
        </div>
      )}

      {inquiries?.length === 0 && (
        <div style={{
          padding: "24px", borderRadius: "10px", textAlign: "center",
          background: C.surface, border: `1px dashed ${C.borderLight}`,
          fontSize: "12.5px", color: C.textMuted, lineHeight: 1.9,
        }}>
          問い合わせはまだありません。
          {isDenryoku && (
            <>
              <br />「質問する」でナレッジ検索しても解決しない場合に、そこから起票できます。
            </>
          )}
        </div>
      )}

      {inquiries?.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {inquiries.map((inq) => (
            <button
              key={inq.inquiry_id}
              onClick={() => navigate(`/inquiry/tickets/${inq.inquiry_id}`)}
              style={{
                display: "flex", alignItems: "center", gap: "14px", textAlign: "left",
                padding: "12px 16px", borderRadius: "10px", width: "100%",
                background: C.surface, border: `1px solid ${C.border}`,
                color: C.text, fontFamily: "inherit", cursor: "pointer",
                transition: "all 0.15s",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = C.surfaceHover; e.currentTarget.style.borderColor = C.borderLight; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = C.surface; e.currentTarget.style.borderColor = C.border; }}
            >
              <span style={{ fontFamily: "monospace", fontSize: "12px", fontWeight: 700, color: C.accent, flexShrink: 0 }}>
                No.{inq.number}
              </span>
              <StatusBadge status={inq.status} />
              <span style={{
                flexShrink: 0, padding: "1px 8px", borderRadius: "4px", fontSize: "10px",
                color: C.textMuted, border: `1px solid ${C.border}`,
              }}>
                {inq.category}
              </span>
              <span style={{
                flex: 1, minWidth: 0, fontSize: "12.5px", lineHeight: 1.6,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {inq.content}
              </span>
              <span style={{ flexShrink: 0, fontSize: "11px", color: C.textMuted }}>{inq.requester}</span>
              <span style={{ flexShrink: 0, fontSize: "10.5px", color: C.textDim, fontFamily: "monospace" }}>
                {formatTimestamp(inq.updated_at)}
              </span>
            </button>
          ))}
        </div>
      )}
    </InquiryShell>
  );
}
