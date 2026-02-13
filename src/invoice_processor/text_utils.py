import re
from typing import Any, Optional


def clean_project_name(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    star_positions = [idx for idx, ch in enumerate(text) if ch == "*"]
    if len(star_positions) >= 2:
        after = text[star_positions[1] + 1 :]
        match = re.match(r"[\u4e00-\u9fff]+", after)
        if match:
            return match.group(0)
    return value


def extract_reconcile_vat_num(text: Any) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    pattern = r"被红冲蓝字.{0,12}?发票号码[:：]?\s*(\d+)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    return None


def normalize_bank_receipt_uid(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if trimmed == "":
        return value
    return re.sub(r"[^A-Za-z0-9]+$", "", trimmed)


def normalize_direction(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip().lower()
    if raw in {"in", "out"}:
        return raw
    if raw in {"借", "付", "付款", "支出", "转出"}:
        return "out"
    if raw in {"贷", "收", "收款", "收入", "转入"}:
        return "in"
    return value
