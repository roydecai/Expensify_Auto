import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from text_utils import normalize_bank_receipt_uid

JsonDict = Dict[str, Any]


def load_spec(spec_path: Union[str, Path]) -> JsonDict:
    spec_path = Path(spec_path)
    with open(spec_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _is_blank_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == ""


def _contains_latin_letter(text: str) -> bool:
    return re.search(r"[A-Za-z]", text) is not None


def _contains_cjk(text: str) -> bool:
    return re.search(r"[\u4e00-\u9fff]", text) is not None


def _is_cjk_only_name(text: str) -> bool:
    if _contains_latin_letter(text):
        return False
    return _contains_cjk(text)


def _preview(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[:limit]


@dataclass(frozen=True)
class Finding:
    code: str
    field: Optional[str]
    message: str
    rule: Optional[str] = None

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {"code": self.code, "message": self.message}
        if self.field is not None:
            payload["field"] = self.field
        if self.rule is not None:
            payload["rule"] = self.rule
        return payload


def validate_extracted_json(
    json_path: Union[str, Path],
    spec: JsonDict,
    *,
    pdf_path: Optional[Union[str, Path]] = None,
    today: Optional[date] = None,
    extracted_text_preview_len: int = 1000,
) -> JsonDict:
    today = today or date.today()
    json_path = Path(json_path)
    pdf_filename = Path(pdf_path).name if pdf_path else None

    global_rules = spec.get("global_rules", {})
    status_model = spec.get("status_model", {})

    errors: List[Finding] = []
    warnings: List[Finding] = []

    raw_text_preview = ""
    document_type: str = "unknown"
    parsed: Optional[JsonDict] = None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            parsed_any = json.load(f)
    except Exception:
        return {
            "status": status_model.get("fail_human", "FAIL_HUMAN"),
            "document_type": "unknown",
            "errors": [
                Finding(
                    code=global_rules.get("json_must_parse", {}).get("error_code", "JSON_PARSE_ERROR"),
                    field=None,
                    message="JSON 无法解析",
                    rule="json_must_parse",
                ).to_dict()
            ],
            "warnings": [],
            "context": {
                "pdf_filename": pdf_filename,
                "json_filename": json_path.name,
                "extracted_text_preview": "",
            },
        }

    if not isinstance(parsed_any, dict):
        return {
            "status": status_model.get("fail_human", "FAIL_HUMAN"),
            "document_type": "unknown",
            "errors": [
                Finding(
                    code=global_rules.get("root_must_be_object", {}).get("error_code", "ROOT_NOT_OBJECT"),
                    field=None,
                    message="顶层 JSON 不是 object",
                    rule="root_must_be_object",
                ).to_dict()
            ],
            "warnings": [],
            "context": {
                "pdf_filename": pdf_filename,
                "json_filename": json_path.name,
                "extracted_text_preview": "",
            },
        }

    parsed = parsed_any
    doc_type_value = parsed.get("document_type")
    if isinstance(doc_type_value, str):
        document_type = doc_type_value

    allowed_doc_types = set(global_rules.get("document_type_enum", {}).get("allowed", []))
    if document_type not in allowed_doc_types:
        errors.append(
            Finding(
                code=global_rules.get("document_type_enum", {}).get("error_code", "DOC_TYPE_INVALID"),
                field="document_type",
                message=f"document_type 不在允许范围内: {document_type}",
                rule="document_type_enum",
            )
        )

    unknown_rule = global_rules.get("document_type_unknown_is_human", {})
    if document_type == unknown_rule.get("value", "unknown"):
        return {
            "status": unknown_rule.get("fail_status", status_model.get("fail_human", "FAIL_HUMAN")),
            "document_type": document_type,
            "errors": [
                Finding(
                    code=unknown_rule.get("error_code", "DOC_TYPE_UNKNOWN"),
                    field="document_type",
                    message="document_type 为 unknown，需人工介入",
                    rule="document_type_unknown_is_human",
                ).to_dict()
            ],
            "warnings": [],
            "context": {
                "pdf_filename": pdf_filename,
                "json_filename": json_path.name,
                "extracted_text_preview": "",
            },
        }

    extracted_text = parsed.get("extracted_text")
    if not isinstance(extracted_text, str) or extracted_text.strip() == "":
        empty_rule = global_rules.get("extracted_text_trim_nonempty", {})
        return {
            "status": empty_rule.get("fail_status", status_model.get("fail_human", "FAIL_HUMAN")),
            "document_type": document_type,
            "errors": [
                Finding(
                    code=empty_rule.get("error_code", "EXTRACTED_TEXT_EMPTY"),
                    field="extracted_text",
                    message="extracted_text 为空或仅空白字符，需人工介入",
                    rule="extracted_text_trim_nonempty",
                ).to_dict()
            ],
            "warnings": [],
            "context": {
                "pdf_filename": pdf_filename,
                "json_filename": json_path.name,
                "extracted_text_preview": "",
            },
        }

    raw_text_preview = _preview(extracted_text, extracted_text_preview_len)
    warning_text_rule = global_rules.get("warnings", {}).get("text_too_short", {})
    threshold_len = warning_text_rule.get("threshold_len")
    if isinstance(threshold_len, int) and len(extracted_text) < threshold_len:
        warnings.append(
            Finding(
                code=warning_text_rule.get("warning_code", "TEXT_TOO_SHORT"),
                field=warning_text_rule.get("field", "extracted_text"),
                message=f"extracted_text 长度<{threshold_len}",
                rule="warnings.text_too_short",
            )
        )

    schema = spec.get("document_schemas", {}).get(document_type)
    if not isinstance(schema, dict):
        errors.append(
            Finding(
                code="SCHEMA_NOT_FOUND",
                field="document_type",
                message=f"未找到 document_type 对应的 schema: {document_type}",
                rule="document_schemas",
            )
        )
        return _finalize_report(
            spec=spec,
            json_path=json_path,
            pdf_filename=pdf_filename,
            document_type=document_type,
            extracted_text_preview=raw_text_preview,
            errors=errors,
            warnings=warnings,
        )

    required_fields = schema.get("required_fields", [])
    if isinstance(required_fields, list):
        for field in required_fields:
            if not isinstance(field, str):
                continue
            if field not in parsed:
                errors.append(
                    Finding(
                        code="REQUIRED_FIELD_MISSING",
                        field=field,
                        message=f"{document_type} 缺少必须字段 {field}",
                        rule="required_fields",
                    )
                )
                continue
            value = parsed.get(field)
            if value is None or _is_blank_string(value):
                errors.append(
                    Finding(
                        code="REQUIRED_FIELD_EMPTY",
                        field=field,
                        message=f"{document_type} 必须字段 {field} 为空",
                        rule="required_fields",
                    )
                )

    optional_fields = schema.get("optional_fields", [])
    if isinstance(optional_fields, list):
        for opt in optional_fields:
            if not isinstance(opt, dict):
                continue
            field = opt.get("field")
            if not isinstance(field, str):
                continue
            missing = field not in parsed or parsed.get(field) is None or _is_blank_string(parsed.get(field))
            if not missing:
                continue
            when_missing = opt.get("when_missing", {})
            if not isinstance(when_missing, dict):
                continue
            if when_missing.get("severity") == "warning":
                warnings.append(
                    Finding(
                        code=str(when_missing.get("code", "MISSING_OPTIONAL")),
                        field=field,
                        message=f"{document_type} 可选字段 {field} 缺失或为空",
                        rule="optional_fields",
                    )
                )

    field_bindings = schema.get("field_bindings", {})
    if isinstance(field_bindings, dict):
        field_rules = spec.get("field_rules", {})
        for field, binding in field_bindings.items():
            if field not in parsed:
                continue
            value = parsed.get(field)
            rule_name, related_name_field = _normalize_binding(binding)
            rule = field_rules.get(rule_name) if isinstance(rule_name, str) else None
            if not isinstance(rule, dict):
                continue

            if rule_name == "date":
                _validate_date(field, value, rule, today, errors)
            elif rule_name == "amount_like":
                _validate_regex(field, value, rule, "format", errors)
            elif rule_name == "uid":
                if document_type == "bank_receipt" and rule.get("bank_receipt_trim_trailing_symbols") is True:
                    value = normalize_bank_receipt_uid(value)
                _validate_regex(field, value, rule, "format", errors)
            elif rule_name == "currency":
                _validate_currency(field, value, rule, errors)
            elif rule_name == "direction":
                _validate_enum(field, value, rule, "DIRECTION_INVALID", errors)
            elif rule_name == "name_like":
                _validate_name_like(field, value, rule, errors, warnings)
            elif rule_name == "tax_id":
                related_name_value = None
                if isinstance(related_name_field, str):
                    related_name_value = parsed.get(related_name_field)
                _validate_tax_id(field, value, rule, related_name_value, errors)

    return _finalize_report(
        spec=spec,
        json_path=json_path,
        pdf_filename=pdf_filename,
        document_type=document_type,
        extracted_text_preview=raw_text_preview,
        errors=errors,
        warnings=warnings,
    )


def _normalize_binding(binding: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(binding, str):
        return binding, None
    if isinstance(binding, dict):
        rule = binding.get("rule")
        related_name_field = binding.get("related_name_field")
        return rule if isinstance(rule, str) else None, related_name_field if isinstance(related_name_field, str) else None
    return None, None


def _validate_regex(field: str, value: Any, rule: JsonDict, code_key: str, errors: List[Finding]) -> None:
    if not isinstance(value, str) or value.strip() == "":
        return
    pattern = rule.get("regex")
    if not isinstance(pattern, str):
        return
    if re.fullmatch(pattern, value.strip()) is None:
        codes = rule.get("error_codes", {})
        code = codes.get(code_key, "FORMAT_INVALID") if isinstance(codes, dict) else "FORMAT_INVALID"
        errors.append(
            Finding(
                code=code,
                field=field,
                message=f"{field} 格式不合法",
                rule=rule.get("regex") if isinstance(rule.get("regex"), str) else None,
            )
        )


def _validate_currency(field: str, value: Any, rule: JsonDict, errors: List[Finding]) -> None:
    if not isinstance(value, str) or value.strip() == "":
        return
    allowed = rule.get("allowed", [])
    if not isinstance(allowed, list):
        return
    if value.strip() not in set([v for v in allowed if isinstance(v, str)]):
        codes = rule.get("error_codes", {})
        code = codes.get("invalid", "CURRENCY_INVALID") if isinstance(codes, dict) else "CURRENCY_INVALID"
        errors.append(Finding(code=code, field=field, message=f"{field} 不在允许币种白名单中", rule="currency.allowed"))


def _validate_enum(field: str, value: Any, rule: JsonDict, default_code: str, errors: List[Finding]) -> None:
    if not isinstance(value, str) or value.strip() == "":
        return
    allowed = rule.get("allowed", [])
    if not isinstance(allowed, list):
        return
    if value.strip() not in set([v for v in allowed if isinstance(v, str)]):
        codes = rule.get("error_codes", {})
        code = codes.get("invalid", default_code) if isinstance(codes, dict) else default_code
        errors.append(Finding(code=code, field=field, message=f"{field} 不在允许范围内", rule="direction.allowed"))


def _validate_date(field: str, value: Any, rule: JsonDict, today: date, errors: List[Finding]) -> None:
    if not isinstance(value, str) or value.strip() == "":
        return
    codes = rule.get("error_codes", {}) if isinstance(rule.get("error_codes"), dict) else {}

    regex = rule.get("regex")
    if isinstance(regex, str) and re.fullmatch(regex, value.strip()) is None:
        errors.append(Finding(code=codes.get("format", "DATE_FORMAT_INVALID"), field=field, message="date 格式不合法", rule="date.regex"))
        return
    try:
        parsed_date = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except Exception:
        errors.append(Finding(code=codes.get("format", "DATE_FORMAT_INVALID"), field=field, message="date 无法解析", rule="date.format"))
        return

    range_cfg = rule.get("range_days_relative_to_today", {})
    if isinstance(range_cfg, dict):
        past_days = range_cfg.get("past_days")
        future_days = range_cfg.get("future_days")
        if isinstance(past_days, int) and isinstance(future_days, int):
            min_date = today - timedelta(days=past_days)
            max_date = today + timedelta(days=future_days)
            if parsed_date < min_date or parsed_date > max_date:
                errors.append(
                    Finding(
                        code=codes.get("out_of_range", "DATE_OUT_OF_RANGE"),
                        field=field,
                        message="date 超出允许范围",
                        rule="date.range_days_relative_to_today",
                    )
                )


def _validate_name_like(field: str, value: Any, rule: JsonDict, errors: List[Finding], warnings: List[Finding]) -> None:
    if not isinstance(value, str):
        return
    text = value.strip()
    if text == "":
        return

    char_policy = rule.get("char_policy", {})
    error_codes = rule.get("error_codes", {}) if isinstance(rule.get("error_codes"), dict) else {}
    warning_codes = rule.get("warning_codes", {}) if isinstance(rule.get("warning_codes"), dict) else {}

    if isinstance(char_policy, dict) and char_policy.get("disallow_quotes_anywhere"):
        if re.search(r"[\"'“”‘’]", text):
            errors.append(Finding(code=error_codes.get("has_quote", "NAME_HAS_QUOTE"), field=field, message=f"{field} 包含引号", rule="name_like.char_policy"))
            return

    name_check = _check_name_char_policy(text, char_policy if isinstance(char_policy, dict) else {})
    if name_check is not None:
        code_key, message = name_check
        if code_key == "ws_outside_parentheses":
            errors.append(Finding(code=error_codes.get("ws_outside_parentheses", "NAME_HAS_WHITESPACE_OUTSIDE_BRACKETS"), field=field, message=message, rule="name_like.char_policy"))
        elif code_key == "parentheses_unbalanced":
            errors.append(Finding(code=error_codes.get("parentheses_unbalanced", "NAME_BRACKETS_UNBALANCED"), field=field, message=message, rule="name_like.char_policy"))
        else:
            errors.append(Finding(code=error_codes.get("has_punctuation", "NAME_HAS_PUNCTUATION"), field=field, message=message, rule="name_like.char_policy"))
        return

    length_cfg = rule.get("length", {})
    if isinstance(length_cfg, dict):
        min_len = length_cfg.get("min")
        max_len = length_cfg.get("max")
        if isinstance(min_len, int) and len(text) < min_len:
            warnings.append(Finding(code=warning_codes.get("length_suspicious", "NAME_LENGTH_SUSPICIOUS"), field=field, message=f"{field} 长度过短", rule="name_like.length"))
        if isinstance(max_len, int) and len(text) > max_len:
            warnings.append(Finding(code=warning_codes.get("length_suspicious", "NAME_LENGTH_SUSPICIOUS"), field=field, message=f"{field} 长度过长", rule="name_like.length"))

    noise_keywords = ["发票号码", "开票日期", "合计", "价税合计", "纳税人识别号", "统一社会信用代码", "金额", "税额"]
    if any(k in text for k in noise_keywords):
        warnings.append(Finding(code=warning_codes.get("noise_keywords", "NAME_NOISE_KEYWORDS"), field=field, message=f"{field} 可能包含噪声字段名", rule="name_like.warning.noise_keywords"))

    payload_chars = [c for c in text if c not in ["(", ")", "（", "）"] and not c.isspace()]
    if payload_chars:
        digit_count = sum(1 for c in payload_chars if c.isdigit())
        if digit_count / len(payload_chars) >= 0.8:
            warnings.append(Finding(code=warning_codes.get("mostly_numeric", "NAME_MOSTLY_NUMERIC"), field=field, message=f"{field} 主要由数字构成", rule="name_like.warning.mostly_numeric"))


def _check_name_char_policy(text: str, policy: JsonDict) -> Optional[Tuple[str, str]]:
    open_to_close = {"(": ")", "（": "）"}
    closes = {")", "）"}
    expected_close: Optional[str] = None
    depth = 0
    english_only = _is_english_name_for_policy(text)

    for i, ch in enumerate(text):
        if ch in open_to_close:
            if depth == 1 and policy.get("disallow_parentheses_nesting", True):
                return "parentheses_unbalanced", "括号不允许嵌套"
            expected_close = open_to_close[ch]
            depth = 1
            continue
        if ch in closes:
            if depth == 0 or expected_close != ch:
                return "parentheses_unbalanced", "括号不成对或类型不匹配"
            expected_close = None
            depth = 0
            continue

        if ch.isspace():
            if depth == 0 and policy.get("disallow_whitespace_outside_parentheses", True):
                if not english_only:
                    return "ws_outside_parentheses", "括号外不允许空白字符"
                prev_idx = i - 1
                next_idx = i + 1
                while prev_idx >= 0 and text[prev_idx].isspace():
                    prev_idx -= 1
                while next_idx < len(text) and text[next_idx].isspace():
                    next_idx += 1
                if prev_idx < 0 or next_idx >= len(text):
                    return "ws_outside_parentheses", "括号外不允许空白字符"
                prev_ch = text[prev_idx]
                next_ch = text[next_idx]
                allowed_neighbors = (prev_ch.isalnum() or prev_ch in ".,") and (next_ch.isalnum() or next_ch in ".,")
                if not allowed_neighbors:
                    return "ws_outside_parentheses", "括号外不允许空白字符"
            continue

        if ch.isdigit() or ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("\u4e00" <= ch <= "\u9fff"):
            continue
        if english_only and ch in ".,": 
            continue

        return "has_punctuation", "包含不允许的标点或符号"

    if depth != 0 and policy.get("require_parentheses_balanced", True):
        return "parentheses_unbalanced", "括号不成对"
    return None


def _is_english_name_for_policy(text: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", text):
        return False
    if not re.search(r"[A-Za-z]", text):
        return False
    return True


def _validate_tax_id(field: str, value: Any, rule: JsonDict, related_name_value: Any, errors: List[Finding]) -> None:
    if not isinstance(value, str) or value.strip() == "":
        return
    base_pattern = rule.get("regex")
    if isinstance(base_pattern, str) and re.fullmatch(base_pattern, value.strip()) is None:
        codes = rule.get("error_codes", {}) if isinstance(rule.get("error_codes"), dict) else {}
        errors.append(Finding(code=codes.get("format", "TAX_ID_FORMAT_INVALID"), field=field, message=f"{field} 格式不合法", rule="tax_id.regex"))
        return

    conditional_rules = rule.get("conditional_rules", [])
    if not isinstance(conditional_rules, list):
        return

    related_name = related_name_value if isinstance(related_name_value, str) else ""
    for cond in conditional_rules:
        if not isinstance(cond, dict):
            continue
        when = cond.get("when", {})
        then = cond.get("then", {})
        if not isinstance(when, dict) or not isinstance(then, dict):
            continue
        if when.get("related_name_script") == "cjk_only":
            if _is_cjk_only_name(related_name):
                pattern = then.get("regex")
                if isinstance(pattern, str) and re.fullmatch(pattern, value.strip()) is None:
                    errors.append(
                        Finding(
                            code=str(then.get("error_code", "TAX_ID_CJK_NAME_MUST_BE_18")),
                            field=field,
                            message=f"{field} 在中文名称场景下必须为18位",
                            rule="tax_id.conditional_rules.cjk_only",
                        )
                    )


def _finalize_report(
    *,
    spec: JsonDict,
    json_path: Path,
    pdf_filename: Optional[str],
    document_type: str,
    extracted_text_preview: str,
    errors: List[Finding],
    warnings: List[Finding],
) -> JsonDict:
    status_model = spec.get("status_model", {})
    status = status_model.get("pass", "PASS")
    if errors:
        status = status_model.get("fail_llm", "FAIL_LLM")

    report = {
        "status": status,
        "document_type": document_type,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
        "context": {
            "pdf_filename": pdf_filename,
            "json_filename": json_path.name,
            "extracted_text_preview": extracted_text_preview,
        },
        "spec_version": spec.get("spec_version"),
    }
    return report


def validate_dir(
    json_dir: Union[str, Path],
    spec: JsonDict,
    *,
    extracted_text_preview_len: int = 1000,
    today: Optional[date] = None,
) -> Tuple[JsonDict, List[JsonDict]]:
    today = today or date.today()
    json_dir = Path(json_dir)
    json_files = sorted(json_dir.glob("*_extracted_revised.json"))

    reports: List[JsonDict] = []
    summary = {
        "spec_version": spec.get("spec_version"),
        "dir": str(json_dir),
        "total": 0,
        "pass": 0,
        "fail_human": 0,
        "fail_llm": 0,
        "errors_by_code": {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    for json_path in json_files:
        report = validate_extracted_json(
            json_path,
            spec,
            today=today,
            extracted_text_preview_len=extracted_text_preview_len,
        )
        summary["total"] += 1
        status = report.get("status")
        if status == spec.get("status_model", {}).get("pass", "PASS"):
            summary["pass"] += 1
        elif status == spec.get("status_model", {}).get("fail_human", "FAIL_HUMAN"):
            summary["fail_human"] += 1
        else:
            summary["fail_llm"] += 1

        for err in report.get("errors", []):
            code = err.get("code")
            if not isinstance(code, str):
                continue
            summary["errors_by_code"][code] = summary["errors_by_code"].get(code, 0) + 1

        reports.append(report)

    return summary, reports
