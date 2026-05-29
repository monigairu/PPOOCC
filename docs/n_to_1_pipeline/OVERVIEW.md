# N対1 転記パイプライン 全体概要

作成日：2026-05-23  
対象：複数の委託会社資料（Excel / Word / PDF）から NuRO 様式（MRC1）への自動転記  
立て付け：事前レビュー機能（/review）と並行して動かす PoC 拡張

---

## 何を作るか

現状の転記機能（`/`画面）は「1ファイル → MRC1」の1対1。  
今回は「N ファイル → MRC1」の N 対 1 に拡張する。

委託会社が提出する書類は会社によってフォーマットが異なる。  
同じ情報が「物量データ（Excel）」「参考見積書（PDF）」「工程表（Excel）」の 3 ファイルに分散していることが典型例。

```
【現状】
資料 1 ファイル → AI 抽出 → MRC1 転記

【今回】
資料 N ファイル ──┐
（Excel / Word / PDF）   ├→ AI 抽出 → マージ → 計算検証 → MRC1 転記
                  ─┘
```

---

## アーキテクチャの役割分担

| 役割 | 担当 | 禁止事項 |
|---|---|---|
| 資料からの値・計算仕様の抽出 | Gemini 3.5 Flash（structured output） | 算術計算を絶対にさせない |
| 計算の実行・検証 | `formula_executor.py`（Python） | LLM に計算させない。特定の式をハードコードしない |
| 単位変換（円→千円など） | `unit_converter.py`（書き込み直前のみ） | 抽出・マージ中は円のまま扱う |
| セル番地の決定 | YAML ルックアップ（`frames/frameB/MRC1.yaml`） | Gemini にセル番地を判断させない |
| MRC1 への書き込み | `form_generation_pipeline.py`（既存を拡張） | `writable: false` のセルには書かない |

---

## Phase 構成

| Phase | 内容 | 状態 |
|---|---|---|
| Phase 1 | 基盤層（formula_executor・unit_converter・writable フラグ） | **完了** |
| Phase 2 | Reader 層（PDF リーダー・Excel/Word ラッパー・SourceDocument） | 実装予定 |
| Phase 3 | 抽出拡張（mapper.py/validator.py への FormulaSpec・structured output 追加） | 実装予定 |
| Phase 4 | マージ＆書込（field_merger・form_generation_pipeline 拡張） | 実装予定 |
| Phase 5 | API＆スクリプト（非同期エンドポイント・手動確認スクリプト） | 実装予定 |

---

## 既存資産との関係

新規で作るのは以下の 4 要素のみ。その他は既存コードの拡張。

| 新規 | 既存の拡張 |
|---|---|
| PDF リーダー（`app/readers/pdf_reader.py`） | Excel / Word パーサー（`parser.py` をラップ） |
| `formula_executor.py` | `mapper.py` / `validator.py`（FormulaSpec 抽出を追加） |
| `unit_converter.py` | `form_generation_pipeline.py`（writable・単位変換・max_rows を追加） |
| `merger/field_merger.py` | `tabular_handler.py`（費用列の単位変換を追加） |

---

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `docs/n_to_1_pipeline/OVERVIEW.md` | このファイル |
| `docs/n_to_1_pipeline/PHASE1_SUMMARY.md` | Phase 1 実装まとめ（完了） |
| `docs/n_to_1_pipeline/PHASE2_SUMMARY.md` | Phase 2 実装まとめ（Reader 層） |
| `docs/n_to_1_pipeline/PHASE3_SUMMARY.md` | Phase 3 実装まとめ（抽出拡張） |
| `docs/n_to_1_pipeline/PHASE4_SUMMARY.md` | Phase 4 実装まとめ（マージ＆書込） |
| `docs/n_to_1_pipeline/PHASE5_SUMMARY.md` | Phase 5 実装まとめ（API＆スクリプト） |
| `docs/claude_code_prompt_n_to_1_pipeline.md` | Claude Code 向け実装タスク詳細 |
