/**
 * /api/inquiry の fetch ラッパ（DESIGN §2・エンドポイントとエラー整形の一元管理）
 *
 * エラー方針（DESIGN §6 のフロント側）：
 *   - 接続不可・HTTPエラーはすべて ApiError に正規化する（status=0 は接続不可）。
 *   - 棄却（abstained）はエラーではなく正常なレスポンス（呼び出し側が起票導線を出す）。
 */
const API_BASE = "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status; // HTTPステータス。接続不可は 0
  }
}

async function request(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    // fetch 自体の失敗＝接続不可（メッセージ文字列はブラウザ依存のため型で判定しない）
    throw new ApiError("バックエンドに接続できません。起動しているか確認してください。", 0);
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    // FastAPI の detail は文字列（404/409/502）または配列（422）
    const detail = Array.isArray(err.detail)
      ? err.detail.map((d) => d.msg || JSON.stringify(d)).join(" / ")
      : err.detail;
    throw new ApiError(detail || `サーバーエラー（HTTP ${res.status}）`, res.status);
  }
  return res.status === 204 ? null : res.json();
}

/** (a) 質問 → 引用付き回答 or 棄却（POST /api/inquiry/ask） */
export const askQuestion = (question, utility) =>
  request("/inquiry/ask", { method: "POST", body: { question, utility } });

/** (b) 起票（POST /api/inquiry）→ { inquiry_id, number } */
export const createInquiry = ({ category, content, requester, selfSolveLog }) =>
  request("/inquiry", {
    method: "POST",
    body: { category, content, requester, self_solve_log: selfSolveLog ?? null },
  });

/** (b) 一覧。requester 指定=自分の分のみ（電力）／未指定=全件（NuRO） */
export const listInquiries = (requester) =>
  request(`/inquiry${requester ? `?requester=${encodeURIComponent(requester)}` : ""}`);

/** (b) 詳細1件 */
export const getInquiry = (inquiryId) =>
  request(`/inquiry/${encodeURIComponent(inquiryId)}`);

/** (b) NuRO回答登録（open→answered） */
export const submitAnswer = (inquiryId, { content, answeredBy }) =>
  request(`/inquiry/${encodeURIComponent(inquiryId)}/answer`, {
    method: "POST",
    body: { content, answered_by: answeredBy },
  });

/** (b) 電力側の状態遷移：resolved=解決確認／open=差し戻し（D-15） */
export const updateStatus = (inquiryId, status) =>
  request(`/inquiry/${encodeURIComponent(inquiryId)}/status`, {
    method: "PATCH",
    body: { status },
  });
