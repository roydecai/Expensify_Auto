import re
from typing import Any


def normalize_date_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return value
    normalized = re.sub(r"[年月]", "-", trimmed)
    normalized = re.sub(r"日", "", normalized)
    normalized = re.sub(r"[\/_]", "-", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    if re.fullmatch(r"\d{8}", normalized):
        return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
        year, month, day = normalized.split("-")
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return normalized
