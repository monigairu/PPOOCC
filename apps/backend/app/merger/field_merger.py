"""
N:1 マージ・競合解決モジュール

複数ファイルの抽出結果を1つのマージ済み辞書にまとめる。
優先順位に基づいて値を選び、競合は conflicts リストに記録する。
"""

# ソースタイプ別の優先順位（小さいほど優先）
# TODO(PoC後): YAML 外部化して会社・様式ごとに設定できるようにする
SOURCE_PRIORITY: dict[str, int] = {
    "見積書": 1,
    "工程表": 2,
    "物量データ": 3,
    "その他": 99,
}

# フィールドごとにソースを強制指定するオーバーライドテーブル
FIELD_SOURCE_OVERRIDE: dict[str, str] = {
    "工期開始日": "工程表",
    "工期終了日": "工程表",
    "総額": "見積書",
    "実施内容": "見積書",
}


def _source_priority(document_kind: str) -> int:
    return SOURCE_PRIORITY.get(document_kind, SOURCE_PRIORITY["その他"])


def merge_extractions(
    extractions: list[dict],
) -> tuple[dict, list[dict]]:
    """
    複数ソースの抽出結果をマージする。

    各 extraction は map_to_schema_from_doc の戻り値形式を想定:
        {
            "source_file": "見積書.pdf",
            "document_kind": "見積書",
            "data": { フィールド名: 値, ... },
            "_metadata": { フィールド名: { source_location, confidence }, ... },
            "formula_specs": [...],
        }

    Returns:
        merged:    { フィールド名: { value, source_file, confidence, source_location }, ... }
        conflicts: [ { field, candidates: [...] }, ... ]
    """
    # フィールドごとに候補をまとめる
    # candidates[field] = [ { value, source_file, document_kind, confidence, source_location }, ... ]
    candidates: dict[str, list[dict]] = {}

    for ext in extractions:
        source_file = ext.get("source_file", "不明")
        document_kind = ext.get("document_kind", "その他")
        data = ext.get("data", {})
        metadata = ext.get("_metadata", {})

        for field, value in data.items():
            if value is None:
                continue
            if isinstance(value, list) and len(value) == 0:
                continue

            field_meta = metadata.get(field, {})
            if isinstance(field_meta, dict):
                confidence = field_meta.get("confidence", "unknown")
                source_location = field_meta.get("source_location")
            else:
                confidence = "unknown"
                source_location = None

            candidates.setdefault(field, []).append({
                "value": value,
                "source_file": source_file,
                "document_kind": document_kind,
                "confidence": confidence,
                "source_location": source_location,
            })

    merged: dict[str, dict] = {}
    conflicts: list[dict] = []

    for field, cands in candidates.items():
        if len(cands) == 1:
            c = cands[0]
            merged[field] = {
                "value": c["value"],
                "source_file": c["source_file"],
                "confidence": c["confidence"],
                "source_location": c["source_location"],
            }
            continue

        # FIELD_SOURCE_OVERRIDE が指定されているフィールドはそのソースを強制選択
        override_kind = FIELD_SOURCE_OVERRIDE.get(field)
        if override_kind:
            preferred = [c for c in cands if c["document_kind"] == override_kind]
            chosen = preferred[0] if preferred else sorted(cands, key=lambda c: _source_priority(c["document_kind"]))[0]
        else:
            chosen = sorted(cands, key=lambda c: _source_priority(c["document_kind"]))[0]

        # 他の候補と値が異なるものを競合として記録
        others = [c for c in cands if c is not chosen]
        has_conflict = any(_values_differ(chosen["value"], c["value"]) for c in others)

        merged[field] = {
            "value": chosen["value"],
            "source_file": chosen["source_file"],
            "confidence": chosen["confidence"],
            "source_location": chosen["source_location"],
        }

        if has_conflict:
            conflicts.append({
                "field": field,
                "candidates": [
                    {"value": c["value"], "source": c["source_file"], "document_kind": c["document_kind"]}
                    for c in cands
                ],
            })

    # 解体機器リストは normalize_equipment_list で別途処理
    equipment_lists = []
    for ext in extractions:
        items = ext.get("data", {}).get("解体機器リスト", [])
        if items:
            equipment_lists.append(items)

    if equipment_lists:
        merged["解体機器リスト"] = {
            "value": normalize_equipment_list(equipment_lists),
            "source_file": "（複数ソース結合）",
            "confidence": "medium",
            "source_location": None,
        }

    return merged, conflicts


def normalize_equipment_list(raw_lists: list[list[dict]]) -> list[dict]:
    """
    複数ソースの解体機器リストを単純結合する（PoC 版）。

    TODO(PoC後): Gemini を使った名寄せ（重複排除）を実装する。
    「配管（50A）」と「既設50A配管撤去」を同一機器として統合する。
    現在は単純結合のみで重複が起きうるが PoC 段階では許容。
    """
    combined = []
    for lst in raw_lists:
        combined.extend(lst)
    return combined


def _values_differ(a, b) -> bool:
    """2つの値が実質的に異なるかを判定する（型を揃えて比較）。"""
    if a == b:
        return False
    # 数値の場合は文字列化して比較
    try:
        return float(str(a).replace(",", "")) != float(str(b).replace(",", ""))
    except (ValueError, TypeError):
        return str(a).strip() != str(b).strip()
