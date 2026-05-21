# Workflow の Session State に使うキー定数
# 各 FunctionNode がこのモジュールを参照することでタイポを防ぐ

# ── 入力（run_workflow() が state_delta で注入） ──────────────────────────────
MAPPINGS      = "mappings"       # list[dict] — 転記結果
UTILITY_NAME  = "utility_name"   # str
FRAME_NAME    = "frame_name"     # str
SHEET_NAME    = "sheet_name"     # str
REACTOR_TYPE  = "reactor_type"   # str | None
FEE_TYPE      = "fee_type"       # str | None

# ── Tool ノード出力 ───────────────────────────────────────────────────────────
F2_KNOWLEDGE    = "f2_knowledge"    # Tool1
F3_OWN          = "f3_own"          # Tool2a
F3_ALL          = "f3_all"          # Tool2b
SUPPLEMENT_INFO = "supplement_info" # Tool4
# Tool3（類似工事）は Phase2 データ入手後に追加予定
# Tool5（計画実績差分）は RuleCheckNode 内で処理

# ── RuleCheckNode 出力 ────────────────────────────────────────────────────────
PLAN_DIFFS        = "plan_diffs"        # list[dict]
RULE_ITEMS        = "rule_items"        # list[dict] — ReviewItem を dict 化したもの
RULE_CELLS        = "rule_cells"        # list[str] — ルール済みセル番地（synthesis で重複除外）
EMPTY_CELLS       = "empty_cells"       # set[str] → list[str] でシリアライズ
PLACEHOLDER_CELLS = "placeholder_cells" # dict[str, str]

# ── 各並列ノードが書き込む個別トレース（SynthesisNode でまとめる） ──────────────
TRACE_F2         = "_trace_f2"
TRACE_F3_OWN     = "_trace_f3_own"
TRACE_F3_ALL     = "_trace_f3_all"
TRACE_SUPPLEMENT = "_trace_supplement"
TRACE_SIMILAR    = "_trace_similar"   # Tool3（スタブ）

# ── SynthesisNode 出力 ────────────────────────────────────────────────────────
REVIEW_ITEMS     = "review_items"     # list[dict] — ReviewItem を dict 化したもの
RETRIEVAL_TRACE  = "retrieval_trace"  # list[dict] — 各 Tool の検索ログ
