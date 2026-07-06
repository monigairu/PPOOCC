"""プロジェクト内の設定ファイル・実行時データ置き場を一元管理する path 定義。

このモジュールは、`config/` と `data/` のどこに何を置くかを Python 側から参照するための入口。
各処理が文字列でパスを直書きすると、ディレクトリ移動時に修正漏れが起きやすい。そのため、
frame YAML、ruleset schema、review criteria、template workbook、実行時 artifact などはここから取得する。

ここではファイルを開いたり、中身を検証したりしない。実際の YAML 読み込みや schema 正規化は
`transcription_config_loader.py` や `ruleset_loader.py` が担当する。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Fixed contracts: output frames, reconciliation checks, empty ruleset schema, templates.
CONFIG_ROOT = PROJECT_ROOT / "config"
FRAMES_CONFIG_ROOT = CONFIG_ROOT / "frames"
RECONCILIATION_CONFIG_ROOT = CONFIG_ROOT / "reconciliation"
RULESETS_SCHEMA_ROOT = CONFIG_ROOT / "rulesets" / "schema"
RULESETS_CONFIG_ROOT = CONFIG_ROOT / "rulesets"
REVIEW_CRITERIA_CONFIG_ROOT = CONFIG_ROOT / "review_criteria"
TEMPLATES_CONFIG_ROOT = CONFIG_ROOT / "templates"

# Runtime/reference data: uploaded inputs, generated artifacts, profiles, knowledge.
DATA_ROOT = PROJECT_ROOT / "data"
INBOX_ROOT = DATA_ROOT / "inbox"
ARTIFACTS_ROOT = DATA_ROOT / "artifacts"
GOLDEN_ROOT = DATA_ROOT / "golden"
KNOWLEDGE_ROOT = DATA_ROOT / "knowledge"
PROFILES_ROOT = DATA_ROOT / "profiles"
PER_JOB_RULESETS_ROOT = DATA_ROOT / "rulesets"

LEGACY_RULEBOOKS_ROOT = KNOWLEDGE_ROOT / "legacy_rulebooks"
EXTRACTION_SCHEMA_ROOT = KNOWLEDGE_ROOT / "extraction_schema"
FORM_GENERATION_ARTIFACTS_ROOT = DATA_ROOT / "form_generation"


def frame_config_path(frame_name: str, sheet_name: str) -> Path:
    """frameごとのsheet定義YAMLの場所を返す。"""
    return FRAMES_CONFIG_ROOT / frame_name / f"{sheet_name}.yaml"


def enums_config_path(frame_name: str) -> Path:
    """frame内で共有するenum定義YAMLの場所を返す。"""
    return FRAMES_CONFIG_ROOT / frame_name / "enums.yaml"


def reconciliation_config_path(frame_name: str) -> Path:
    """frame別の整合チェック定義YAMLの場所を返す。"""
    return RECONCILIATION_CONFIG_ROOT / f"{frame_name}.yaml"


def reconciliation_template_path() -> Path:
    """frame別定義が無いときに使う整合チェックtemplate YAMLの場所を返す。"""
    return RECONCILIATION_CONFIG_ROOT / "template.yaml"


def ruleset_template_path() -> Path:
    """空のruleset schema template YAMLの場所を返す。"""
    return RULESETS_SCHEMA_ROOT / "template.yaml"


def document_role_slot_hints_path() -> Path:
    """文書種別からruleset slot候補を絞るhint YAMLの場所を返す。"""
    return RULESETS_CONFIG_ROOT / "document_role_slot_hints.yaml"


def review_criteria_path(frame_name: str, sheet_name: str) -> Path:
    """AIレビュー基準YAMLの場所をframe名とsheet名から返す。"""
    return REVIEW_CRITERIA_CONFIG_ROOT / frame_name / f"{sheet_name}.yaml"


def template_workbook_path(frame_name: str = "frameB", file_name: str = "RFP添付用_新様式_フレームB_工事概要_ver.5.3.xlsx") -> Path:
    """Excel転記の書き込み元になるtemplate workbookの場所を返す。"""
    return TEMPLATES_CONFIG_ROOT / frame_name / file_name


def form_generation_output_dir() -> Path:
    """生成済みNuRO様式Excelを保存するruntime output directoryを返す。"""
    return FORM_GENERATION_ARTIFACTS_ROOT / "output"


def form_generation_cache_dir() -> Path:
    """template解析結果などのform generation cache directoryを返す。"""
    return FORM_GENERATION_ARTIFACTS_ROOT / "cache"


def upload_dir() -> Path:
    """アップロード原本を一時保存するinbox directoryを返す。"""
    return INBOX_ROOT / "uploaded"


def sample_source_path() -> Path:
    """開発・検証用sample source JSONの場所を返す。"""
    return INBOX_ROOT / "samples" / "sample_source.json"


def golden_workbook_path(file_name: str = "新様式_フレームB_転記結果_ダミー.xlsx") -> Path:
    """golden比較で使う正解Excel workbookの場所を返す。"""
    return GOLDEN_ROOT / "frameB" / file_name


def dev_inputs_root() -> Path:
    """手元検証用input fileを置くdirectoryを返す。"""
    return INBOX_ROOT / "dev_inputs"


def extracted_artifacts_root() -> Path:
    """抽出済みJSONや中間成果物を保存するartifact directoryを返す。"""
    return ARTIFACTS_ROOT / "extracted"


def rule_candidate_profile_path() -> Path:
    """rulesetting候補生成で使うpending answer profile YAMLの場所を返す。"""
    return PROFILES_ROOT / "candidates" / "pending_answers.yaml"


def utility_company_names_path() -> Path:
    """電力会社名の正規化・推定に使うYAMLの場所を返す。"""
    return KNOWLEDGE_ROOT / "utility_company_names.yaml"


def extraction_schema_root() -> Path:
    """reviewerなどが参照する抽出schema directoryを返す。"""
    return EXTRACTION_SCHEMA_ROOT


def legacy_mrc1_rulebook_root() -> Path:
    """MRC1向けの旧rulebook directoryを返す。"""
    return LEGACY_RULEBOOKS_ROOT / "mrc1"


def mrc1_manhour_rates_path() -> Path:
    """MRC1歩掛率YAMLの場所を返す。"""
    return legacy_mrc1_rulebook_root() / "manhour_rates.yaml"


def mrc1_correction_factors_path() -> Path:
    """MRC1補正係数YAMLの場所を返す。"""
    return legacy_mrc1_rulebook_root() / "correction_factors.yaml"


def legacy_mrc2_rulebook_root() -> Path:
    """MRC2向けの旧rulebook directoryを返す。"""
    return LEGACY_RULEBOOKS_ROOT / "mrc2"


def mrc2_breakdown_path() -> Path:
    """MRC2内訳rulebook YAMLの場所を返す。"""
    return legacy_mrc2_rulebook_root() / "breakdown.yaml"