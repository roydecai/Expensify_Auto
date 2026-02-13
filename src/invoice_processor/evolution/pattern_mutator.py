import json
import re
from pathlib import Path
from typing import Any, Dict, List, cast


class PatternMutator:
    def __init__(self, patterns_path: Path) -> None:
        self.patterns_path = patterns_path

    def load(self) -> Dict[str, Any]:
        with open(self.patterns_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ensure_overrides(self, patterns: Dict[str, Any]) -> Dict[str, Any]:
        overrides = patterns.get("field_patterns_overrides")
        if not isinstance(overrides, dict):
            overrides = {}
            patterns["field_patterns_overrides"] = overrides
        return overrides

    def _existing_ids(self, overrides: Dict[str, Any]) -> List[str]:
        ids: List[str] = []
        for fields in overrides.values():
            if not isinstance(fields, dict):
                continue
            for items in fields.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        value = item.get("id")
                        if isinstance(value, str):
                            ids.append(value)
        return ids

    def append_override(
        self,
        patterns: Dict[str, Any],
        doc_type: str,
        field: str,
        regex: str,
        rule_id: str,
        priority: int = 0,
    ) -> Dict[str, Any]:
        re.compile(regex)
        overrides = self._ensure_overrides(patterns)
        existing_ids = set(self._existing_ids(overrides))
        if rule_id in existing_ids:
            raise ValueError(f"Duplicate rule id: {rule_id}")
        doc_bucket = overrides.get(doc_type)
        if not isinstance(doc_bucket, dict):
            doc_bucket = {}
            overrides[doc_type] = doc_bucket
        field_bucket = doc_bucket.get(field)
        if not isinstance(field_bucket, list):
            field_bucket = []
            doc_bucket[field] = field_bucket
        field_bucket.append(
            {
                "id": rule_id,
                "regex": regex,
                "priority": int(priority),
            }
        )
        return patterns

    def apply_mutation(self, patterns: Dict[str, Any], mutation: Dict[str, Any]) -> Dict[str, Any]:
        action = mutation.get("action")
        if action != "append_override_regex":
            raise ValueError(f"Unsupported mutation action: {action}")
        doc_type = mutation.get("doc_type")
        field = mutation.get("field")
        regex = mutation.get("regex")
        rule_id = mutation.get("id")
        priority = mutation.get("priority", 0)
        if not all(isinstance(v, str) and v for v in [doc_type, field, regex, rule_id]):
            raise ValueError("Mutation missing required fields")
        doc_type_str = cast(str, doc_type)
        field_str = cast(str, field)
        regex_str = cast(str, regex)
        rule_id_str = cast(str, rule_id)
        return self.append_override(patterns, doc_type_str, field_str, regex_str, rule_id_str, int(priority))

    def save(self, patterns: Dict[str, Any]) -> None:
        with open(self.patterns_path, "w", encoding="utf-8") as f:
            json.dump(patterns, f, ensure_ascii=False, indent=2)
