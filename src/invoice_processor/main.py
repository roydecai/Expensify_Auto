import argparse
import json
import logging
import importlib
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config import get_company_db_path, get_default_model_id, get_project_root
from date_utils import normalize_date_string
from pdf_extraction_service import PDFExtractionService
from pdf_json_validator import load_spec, validate_dir, validate_extracted_json
from text_utils import (
    clean_project_name,
    extract_reconcile_vat_num,
    normalize_bank_receipt_uid,
    normalize_direction,
)
from evolution.autofix_agent import run_autofix

def load_dotenv() -> None:
    try:
        mod = importlib.import_module("dotenv")
        maybe = getattr(mod, "load_dotenv", None)
        if callable(maybe):
            maybe()
    except Exception:
        return None


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _default_spec_path() -> Path:
    return Path(__file__).with_name("pdf_to_json_spec_v0_3_1.json")


def _resolve_output_dir(output_dir: Optional[str]) -> Path:
    if output_dir is None:
        return get_project_root() / "temp"
    return Path(output_dir)


def _report_path_for_json(json_path: Path) -> Path:
    if json_path.name.endswith("_extracted_revised.json"):
        return json_path.with_name(json_path.name.replace("_extracted_revised.json", "_validation_report.json"))
    return json_path.with_suffix(".validation_report.json")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _ai_status_path(json_dir: Path) -> Path:
    return json_dir / "ai_iteration_status.json"


def _write_ai_iteration_status(json_dir: Path, payload: Dict[str, Any]) -> Optional[Path]:
    path = _ai_status_path(json_dir)
    _write_json(path, payload)
    return path


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _truncate_text(text, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _apply_bank_receipt_postprocess(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("document_type") != "bank_receipt":
        return payload
    uid = payload.get("uid")
    if isinstance(uid, str) and uid.strip():
        payload["uid"] = normalize_bank_receipt_uid(uid)
    return payload


def _load_company_records() -> List[Dict[str, Optional[str]]]:
    db_path = get_company_db_path()
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT payer_tax_id, full_name, short_name, eng_full_name, eng_short_name FROM companies"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    records: List[Dict[str, Optional[str]]] = []
    for row in rows:
        records.append(
            {
                "payer_tax_id": row[0],
                "full_name": row[1],
                "short_name": row[2],
                "eng_full_name": row[3],
                "eng_short_name": row[4],
            }
        )
    return records


def _normalize_ascii_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).lower()


def _names_match(name: str, record_name: str) -> bool:
    if not name or not record_name:
        return False
    if re.search(r"[\u4e00-\u9fff]", name) or re.search(r"[\u4e00-\u9fff]", record_name):
        return name in record_name or record_name in name
    return _normalize_ascii_name(name) == _normalize_ascii_name(record_name)


def _company_name_matches_any(name: str, record: Dict[str, Optional[str]]) -> bool:
    for key in ("full_name", "short_name", "eng_full_name", "eng_short_name"):
        candidate = record.get(key)
        if isinstance(candidate, str) and _names_match(name, candidate):
            return True
    return False


def _company_mismatch(payload: Dict[str, Any], records: List[Dict[str, Optional[str]]]) -> bool:
    doc_type = payload.get("document_type")
    name = None
    tax_id = None
    if doc_type in {"VAT_invoice", "VAT_invalid_invoice"}:
        name = payload.get("payer")
        tax_id = payload.get("buyer_tax_id")
    elif doc_type == "tax_certificate":
        name = payload.get("payer")
        tax_id = payload.get("payer_tax_id")
    elif doc_type == "bank_receipt":
        payer = payload.get("payer")
        payee = payload.get("payee")
        if isinstance(payer, str):
            name = payer
        if isinstance(payee, str):
            name = name or payee
    if not isinstance(name, str):
        name = None
    if not isinstance(tax_id, str):
        tax_id = None
    if not name and not tax_id:
        return False
    if tax_id:
        for record in records:
            if record.get("payer_tax_id") == tax_id:
                if name:
                    return not _company_name_matches_any(name, record)
                return False
        return True
    if name:
        for record in records:
            if _company_name_matches_any(name, record):
                return False
        if doc_type == "bank_receipt":
            other_name = payload.get("payee") if name == payload.get("payer") else payload.get("payer")
            if isinstance(other_name, str):
                for record in records:
                    if _company_name_matches_any(other_name, record):
                        return False
        return True
    return False


def _apply_company_consistency_checks(
    json_dir: Path, reports: List[Dict[str, Any]], company_records: List[Dict[str, Optional[str]]]
) -> List[Dict[str, Any]]:
    if not company_records:
        return reports
    updated: List[Dict[str, Any]] = []
    for report in reports:
        ctx = report.get("context", {})
        json_filename = ctx.get("json_filename") if isinstance(ctx, dict) else None
        if not isinstance(json_filename, str):
            updated.append(report)
            continue
        json_path = json_dir / json_filename
        try:
            payload = _read_json(json_path)
        except Exception:
            updated.append(report)
            continue
        if not isinstance(payload, dict):
            updated.append(report)
            continue
        if _company_mismatch(payload, company_records):
            new_report = dict(report)
            errors = new_report.get("errors")
            error_list = list(errors) if isinstance(errors, list) else []
            error_list.append(
                {
                    "code": "COMPANY_INFO_MISMATCH",
                    "field": "company",
                    "message": "公司信息与公司库不一致",
                }
            )
            new_report["errors"] = error_list
            new_report["status"] = "FAIL_HUMAN"
            updated.append(new_report)
            continue
        updated.append(report)
    return updated


def _rebuild_summary(spec_version: Any, json_dir: Path, reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "spec_version": spec_version,
        "dir": str(json_dir),
        "total": len(reports),
        "pass": 0,
        "fail_human": 0,
        "fail_llm": 0,
        "errors_by_code": {},
    }
    for report in reports:
        status = report.get("status")
        if status == "PASS":
            summary["pass"] += 1
        elif status == "FAIL_HUMAN":
            summary["fail_human"] += 1
        elif status == "FAIL_LLM":
            summary["fail_llm"] += 1
        for err in report.get("errors", []):
            code = err.get("code") if isinstance(err, dict) else None
            if isinstance(code, str):
                summary["errors_by_code"][code] = summary["errors_by_code"].get(code, 0) + 1
    return summary


def _validation_detail_path(json_dir: Path) -> Path:
    return json_dir / "validation_detail.json"


def _write_validation_detail(
    json_dir: Path, summary: Dict[str, Any], reports: List[Dict[str, Any]]
) -> Optional[Path]:
    filtered = [report for report in reports if report.get("status") == "FAIL_HUMAN"]
    detail_path = _validation_detail_path(json_dir)
    if not filtered:
        if detail_path.exists():
            try:
                detail_path.unlink()
            except Exception:
                pass
        return None
    detail = _rebuild_summary(summary.get("spec_version"), json_dir, filtered)
    detail["reports"] = filtered
    _write_json(detail_path, detail)
    return detail_path


def _human_review_cases_path(json_dir: Path) -> Path:
    return json_dir / "human_review_cases.json"


def _required_fields_by_doc_type(spec: Dict[str, Any]) -> Dict[str, List[str]]:
    schemas = spec.get("document_schemas", {})
    if not isinstance(schemas, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for doc_type, schema in schemas.items():
        if not isinstance(doc_type, str) or not isinstance(schema, dict):
            continue
        required = schema.get("required_fields", [])
        if isinstance(required, list):
            out[doc_type] = [str(x) for x in required if isinstance(x, str)]
    return out


def _write_human_review_cases(
    json_dir: Path,
    *,
    spec: Dict[str, Any],
    spec_path: Path,
    reports: List[Dict[str, Any]],
) -> Optional[Path]:
    cases: List[Dict[str, Any]] = []
    for report in reports:
        if report.get("status") != "FAIL_HUMAN":
            continue
        ctx = report.get("context", {})
        if not isinstance(ctx, dict):
            ctx = {}
        cases.append(
            {
                "pdf_filename": ctx.get("pdf_filename"),
                "json_filename": ctx.get("json_filename"),
                "document_type": report.get("document_type"),
                "errors": report.get("errors", []),
                "warnings": report.get("warnings", []),
                "extracted_text_preview": ctx.get("extracted_text_preview"),
            }
        )
    if not cases:
        return None

    field_rules = spec.get("field_rules", {})
    excerpt = {}
    if isinstance(field_rules, dict):
        for key in ("uid", "date", "amount_like", "amount_like_signed", "name_like", "direction", "tax_id"):
            rule = field_rules.get(key)
            if isinstance(rule, dict):
                excerpt[key] = rule

    payload = {
        "spec_version": spec.get("spec_version"),
        "spec_path": str(spec_path),
        "required_fields_by_document_type": _required_fields_by_doc_type(spec),
        "field_rules_excerpt": excerpt,
        "cases": cases,
        "instructions": [
            "请逐条打开 json_filename 对应的 *_extracted_revised.json，基于 extracted_text 纠正缺失/错误字段。",
            "uid/date/amount/name 字段必须满足 spec 中的格式与字符策略；无法从 extracted_text 确认时请保持为空并标注原因。",
            "修正后重新运行 main.py 对目录做校验，直至 PASS。",
        ],
    }
    path = _human_review_cases_path(json_dir)
    _write_json(path, payload)
    return path


def _cleanup_validation_outputs(json_dir: Path) -> None:
    for report_path in json_dir.glob("*_validation_report.json"):
        try:
            report_path.unlink()
        except Exception:
            continue
    detail_path = _validation_detail_path(json_dir)
    if detail_path.exists():
        try:
            detail_path.unlink()
        except Exception:
            pass


def _report_error_count(report: Dict[str, Any]) -> int:
    errors = report.get("errors")
    return len(errors) if isinstance(errors, list) else 0


def _report_json_filename(report: Dict[str, Any]) -> Optional[str]:
    ctx = report.get("context", {})
    json_filename = ctx.get("json_filename") if isinstance(ctx, dict) else None
    return json_filename if isinstance(json_filename, str) else None


def build_fix_prompt_input(
    report: dict,
    json_dir: Path,
    spec: dict,
    *,
    spec_path: Path,
    fix_text_len: int,
):
    fail_llm = spec.get("status_model", {}).get("fail_llm", "FAIL_LLM")
    if report.get("status") != fail_llm:
        return None

    ctx = report.get("context", {})
    json_filename = ctx.get("json_filename") if isinstance(ctx, dict) else None
    if not isinstance(json_filename, str):
        return None

    json_path = json_dir / json_filename
    if not json_path.exists():
        return None

    try:
        parsed = _read_json(json_path)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    extracted_text = _truncate_text(parsed.get("extracted_text"), fix_text_len)
    current_json = dict(parsed)
    current_json.pop("extracted_text", None)

    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    document_type = report.get("document_type")
    if not isinstance(document_type, str):
        document_type = str(parsed.get("document_type") or "unknown")

    system_prompt = "你是一个严格的财务票据 JSON 修复助手。你的输出会被程序再次校验。"
    user_prompt = "\n".join(
        [
            "目标：在不凭空编造的前提下，基于 extracted_text 修复 current_json，使其满足 spec 的校验规则。",
            "输出要求：只输出修复后的 JSON 对象本身（不要 Markdown、不要代码块、不要解释）。",
            "约束：",
            "- 不要修改 document_type。",
            "- 仅当 extracted_text 明确包含信息时才填写/更正字段；否则保留原值（即使会再次失败）。",
            "- 所有必须字段要求 non-empty（trim 后不为空）。",
            "- 如果出现名称空格问题：括号外不允许空白字符；括号内允许空格。",
            "- direction 只能为 in 或 out。",
            "- date 必须为 YYYY-MM-DD。",
            "",
            f"spec_version: {spec.get('spec_version')}",
            f"spec_path: {str(spec_path)}",
            "",
            "validation_errors:",
            json.dumps(errors, ensure_ascii=False, indent=2) if isinstance(errors, list) else "[]",
            "",
            "validation_warnings:",
            json.dumps(warnings, ensure_ascii=False, indent=2) if isinstance(warnings, list) else "[]",
            "",
            "extracted_text_truncated:",
            extracted_text,
            "",
            "current_json (extracted_text 已移除以减少输入长度):",
            json.dumps(current_json, ensure_ascii=False, indent=2),
        ]
    )

    return {
        "spec_version": spec.get("spec_version"),
        "spec_path": str(spec_path),
        "json_filename": json_filename,
        "document_type": document_type,
        "errors": errors if isinstance(errors, list) else [],
        "warnings": warnings if isinstance(warnings, list) else [],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "meta": {"fix_text_len": fix_text_len},
    }


def _collect_strings(payload: Any) -> Iterable[str]:
    if isinstance(payload, str):
        yield payload
        return
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _collect_strings(value)
        return
    if isinstance(payload, list):
        for value in payload:
            yield from _collect_strings(value)


def _parse_json_from_text(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = raw[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _coerce_response_payload(resp: Any) -> Any:
    if isinstance(resp, (dict, list, str)):
        return resp
    for attr in ("model_dump", "to_dict", "dict"):
        if hasattr(resp, attr):
            try:
                return getattr(resp, attr)()
            except Exception:
                continue
    return resp


def _extract_json_from_response(resp: Any) -> Optional[Dict[str, Any]]:
    payload = _coerce_response_payload(resp)
    for text in _collect_strings(payload):
        parsed = _parse_json_from_text(text)
        if parsed is not None:
            return parsed
    return None


def _extract_longest_text(resp: Any) -> str:
    payload = _coerce_response_payload(resp)
    texts = [t for t in _collect_strings(payload) if isinstance(t, str)]
    if not texts:
        return ""
    return max(texts, key=len)


def _load_ark_settings(default_model: str) -> Dict[str, str]:
    load_dotenv()
    base_url = os.getenv("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
    api_key = os.getenv("ARK_API_KEY") or ""
    model = os.getenv("ARK_MODEL") or default_model
    return {"base_url": base_url, "api_key": api_key, "model": model}


def _create_ark_client(base_url: str, api_key: str) -> Any:
    mod = importlib.import_module("openai")
    openai_cls = getattr(mod, "OpenAI", None)
    if not callable(openai_cls):
        raise RuntimeError("openai.OpenAI 不可用")
    return openai_cls(base_url=base_url, api_key=api_key)


def _apply_llm_fix(
    *,
    fix_input: Dict[str, Any],
    json_dir: Path,
    spec: Dict[str, Any],
    client: Any,
    model: str,
    preview_len: int,
) -> Dict[str, Any]:
    total_start = time.perf_counter()
    json_filename = fix_input.get("json_filename")
    if not isinstance(json_filename, str):
        return {"json_filename": json_filename, "llm_status": "INVALID_INPUT"}

    json_path = json_dir / json_filename
    if not json_path.exists():
        return {"json_filename": json_filename, "llm_status": "JSON_NOT_FOUND"}

    try:
        original = _read_json(json_path)
    except Exception:
        return {"json_filename": json_filename, "llm_status": "JSON_READ_ERROR"}
    if not isinstance(original, dict):
        return {"json_filename": json_filename, "llm_status": "JSON_INVALID"}

    call_start = time.perf_counter()
    try:
        # 使用 OpenAI 兼容接口: chat.completions.create
        response = client.chat.completions.create(
            model=model,
            messages=fix_input.get("messages"),
        )
    except Exception as exc:
        call_seconds = time.perf_counter() - call_start
        total_seconds = time.perf_counter() - total_start
        return {
            "json_filename": json_filename,
            "llm_status": "CALL_ERROR",
            "llm_error": str(exc),
            "llm_call_seconds": round(call_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        }
    call_seconds = time.perf_counter() - call_start

    # 从 OpenAI response 中提取 content
    try:
        content = response.choices[0].message.content
    except Exception:
        content = ""

    parsed = _parse_json_from_text(content)
    if parsed is None:
        response_text = _truncate_text(content, 2000)
        total_seconds = time.perf_counter() - total_start
        return {
            "json_filename": json_filename,
            "llm_status": "PARSE_ERROR",
            "response_text_preview": response_text,
            "llm_call_seconds": round(call_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        }

    merged = dict(original)
    for key, value in parsed.items():
        merged[key] = value
    merged["document_type"] = original.get("document_type")
    if "extracted_text" in original:
        merged["extracted_text"] = original.get("extracted_text")
    if "direction" in merged:
        merged["direction"] = normalize_direction(merged.get("direction"))
    if "date" in merged:
        merged["date"] = normalize_date_string(merged.get("date"))
    if "project_name" in merged:
        merged["project_name"] = clean_project_name(merged.get("project_name"))
    if merged.get("document_type") == "VAT_invalid_invoice" and "reconcile_VAT_num" not in merged:
        extracted_text = original.get("extracted_text")
        reconcile_vat_num = extract_reconcile_vat_num(extracted_text)
        if reconcile_vat_num:
            merged["reconcile_VAT_num"] = reconcile_vat_num
    merged = _apply_bank_receipt_postprocess(merged)

    _write_json(json_path, merged)
    report = validate_extracted_json(json_path, spec, extracted_text_preview_len=preview_len)
    total_seconds = time.perf_counter() - total_start
    return {
        "json_filename": json_filename,
        "llm_status": "OK",
        "validation_status": report.get("status"),
        "validation_errors": report.get("errors", []),
        "llm_call_seconds": round(call_seconds, 3),
        "total_seconds": round(total_seconds, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", help="单个 *_extracted_revised.json 文件或包含这些文件的目录")
    parser.add_argument("--output-dir", default=None, help="当 input_path 为 PDF/目录时，提取 JSON 输出目录")
    parser.add_argument("--workers", type=int, default=4, help="PDF 提取并发线程数")
    parser.add_argument("--sequential", action="store_true", help="顺序提取 PDF 文件")
    parser.add_argument("--spec", default=None, help="校验规则 spec 文件路径（默认使用 v0.3.1）")
    parser.add_argument("--preview-len", type=int, default=1000, help="extracted_text 预览截断长度")
    parser.add_argument("--emit-fix-prompts", action="store_true", help="为 FAIL_LLM 样本生成 LLM 修复提示词输入文件")
    parser.add_argument("--fix-prompts-path", default=None, help="LLM 修复提示词输入输出路径（默认写到目录内）")
    parser.add_argument("--fix-text-len", type=int, default=8000, help="LLM 修复输入中的 extracted_text 截断长度")
    parser.add_argument("--apply-llm", action="store_true", help="调用 LLM 修复 FAIL_LLM 样本并写回 JSON")
    parser.add_argument(
        "--auto-apply-llm",
        dest="auto_apply_llm",
        action="store_true",
        default=True,
        help="检测到 FAIL_LLM 时自动调用 LLM 修复并写回 JSON（默认开启）",
    )
    parser.add_argument(
        "--no-auto-apply-llm",
        dest="auto_apply_llm",
        action="store_false",
        help="关闭自动 LLM 修复（仍可通过 --apply-llm 手动开启）",
    )
    parser.add_argument("--model", default=get_default_model_id(), help="LLM model ID（可用 ARK_MODEL 覆盖）")
    parser.add_argument("--llm-inputs-path", default=None, help="LLM 修复提示词输入路径（默认读目录内 llm_fix_inputs.json）")
    parser.add_argument("--llm-results-path", default=None, help="LLM 修复结果输出路径（默认写到目录内）")
    args = parser.parse_args()

    configure_logging()
    logger = logging.getLogger(__name__)

    spec_path = Path(args.spec) if args.spec else _default_spec_path()
    spec = load_spec(spec_path)
    company_records = _load_company_records()

    input_path = Path(args.input_path)
    pdf_paths: List[Path] = []
    if input_path.is_dir():
        pdf_paths = sorted(input_path.glob("*.pdf"))
    elif input_path.suffix.lower() == ".pdf":
        pdf_paths = [input_path]

    if pdf_paths:
        output_dir = _resolve_output_dir(args.output_dir)
        service = PDFExtractionService()
        if args.sequential or args.workers <= 1:
            service.process_pdfs_sequentially(pdf_paths, output_dir=str(output_dir))
        else:
            service.process_pdfs_multithread(pdf_paths, max_workers=args.workers, output_dir=str(output_dir))
        service.close()
        input_path = output_dir
    if input_path.is_dir():
        summary, reports = validate_dir(input_path, spec, extracted_text_preview_len=args.preview_len)
        reports = _apply_company_consistency_checks(input_path, reports, company_records)
        summary = _rebuild_summary(spec.get("spec_version"), input_path, reports)

        fix_inputs: List[Dict[str, Any]] = []
        llm_results: List[Dict[str, Any]] = []
        llm_enabled = bool(args.apply_llm or args.auto_apply_llm)
        should_apply_llm = llm_enabled
        ai_status = "DISABLED"
        ai_autofix_attempted = False
        ai_autofix_status: Optional[str] = None
        if llm_enabled:
            ai_status = "ENABLED"
        fix_path = Path(args.fix_prompts_path) if args.fix_prompts_path else (input_path / "llm_fix_inputs.json")
        results_path = (
            Path(args.llm_results_path) if args.llm_results_path else (input_path / "llm_fix_results.json")
        )

        max_llm_rounds = 2
        llm_round = 0
        stalled_jsons: set[str] = set()
        last_error_counts: dict[str, int] = {}
        for report in reports:
            if report.get("status") != "FAIL_LLM":
                continue
            json_filename = _report_json_filename(report)
            if isinstance(json_filename, str) and json_filename:
                last_error_counts[json_filename] = _report_error_count(report)

        client: Any = None
        settings: Dict[str, str] = {}
        max_workers = 1
        if should_apply_llm:
            settings = _load_ark_settings(args.model)
            if not settings["api_key"]:
                logger.error("未检测到 ARK_API_KEY，无法自动 LLM 修复，已升级为人工处理")
                for report in reports:
                    if report.get("status") != "FAIL_LLM":
                        continue
                    errors = report.get("errors")
                    error_list = list(errors) if isinstance(errors, list) else []
                    error_list.append(
                        {
                            "code": "LLM_API_KEY_MISSING",
                            "field": "llm_fix",
                            "message": "未配置 ARK_API_KEY，无法自动 LLM 修复",
                        }
                    )
                    report["errors"] = error_list
                    report["status"] = "FAIL_HUMAN"
                ai_status = "DISABLED_MISSING_KEY"
                should_apply_llm = False
            else:
                client = _create_ark_client(settings["base_url"], settings["api_key"])
                cpu_count = os.cpu_count() or 1
                max_workers = max(1, (cpu_count * 2) // 3)
                logger.info(f"LLM 并发线程数: {max_workers}")

        while should_apply_llm and llm_round < max_llm_rounds:
            fix_inputs = []
            for report in reports:
                if report.get("status") != "FAIL_LLM":
                    continue
                json_filename = _report_json_filename(report)
                if not json_filename or json_filename in stalled_jsons:
                    continue
                fix_input = build_fix_prompt_input(
                    report,
                    input_path,
                    spec,
                    spec_path=spec_path,
                    fix_text_len=args.fix_text_len,
                )
                if fix_input is not None:
                    fix_inputs.append(fix_input)

            if args.emit_fix_prompts or fix_inputs:
                _write_json(fix_path, fix_inputs)

            if not fix_inputs:
                break

            llm_round += 1
            logger.info(f"LLM 修复轮次: {llm_round}/{max_llm_rounds}，任务数: {len(fix_inputs)}")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_input = {
                    executor.submit(
                        _apply_llm_fix,
                        fix_input=fix_input,
                        json_dir=input_path,
                        spec=spec,
                        client=client,
                        model=settings["model"],
                        preview_len=args.preview_len,
                    ): fix_input
                    for fix_input in fix_inputs
                }
                for future in as_completed(future_to_input):
                    result = future.result()
                    if isinstance(result, dict):
                        result["llm_round"] = llm_round
                    llm_results.append(result)

            summary, reports = validate_dir(input_path, spec, extracted_text_preview_len=args.preview_len)
            reports = _apply_company_consistency_checks(input_path, reports, company_records)

            for report in reports:
                if report.get("status") != "FAIL_LLM":
                    continue
                json_filename = _report_json_filename(report)
                if not json_filename:
                    continue
                current_errors = _report_error_count(report)
                previous_errors = last_error_counts.get(json_filename)
                if previous_errors is not None and current_errors >= previous_errors:
                    stalled_jsons.add(json_filename)
                last_error_counts[json_filename] = current_errors

        if should_apply_llm and llm_results:
            _write_json(results_path, llm_results)
            durations: List[float] = []
            for item in llm_results:
                if not isinstance(item, dict):
                    continue
                value = item.get("llm_call_seconds")
                if isinstance(value, (int, float)):
                    durations.append(float(value))
            if durations:
                total = sum(durations)
                logger.info(
                    "LLM 调用耗时统计: count=%s avg=%.3fs max=%.3fs",
                    len(durations),
                    total / len(durations),
                    max(durations),
                )

        if should_apply_llm and any(report.get("status") == "FAIL_LLM" for report in reports):
            try:
                result = run_autofix(
                    input_path,
                    spec_path=spec_path,
                    model=args.model,
                )
                logger.info(f"自动迭代结果: {result.get('status')}")
                ai_autofix_attempted = True
                ai_autofix_status = result.get("status") if isinstance(result, dict) else None
                ai_status = "AUTOFIX_ATTEMPTED"
            except Exception:
                logger.exception("自动迭代失败")
                ai_autofix_attempted = True
                ai_autofix_status = "error"
                ai_status = "AUTOFIX_FAILED"

        if should_apply_llm and reports:
            for report in reports:
                if report.get("status") != "FAIL_LLM":
                    continue
                json_filename = _report_json_filename(report)
                if json_filename not in stalled_jsons and llm_round < max_llm_rounds:
                    continue
                errors = report.get("errors")
                error_list = list(errors) if isinstance(errors, list) else []
                error_list.append(
                    {
                        "code": "LLM_FIX_MAX_ROUNDS",
                        "field": "llm_fix",
                        "message": "LLM 修复超过最大轮次仍失败，已升级为人工处理",
                    }
                )
                report["errors"] = error_list
                report["status"] = "FAIL_HUMAN"

        summary = _rebuild_summary(spec.get("spec_version"), input_path, reports)
        logger.info(f"校验完成: total={summary['total']} pass={summary['pass']} fail_human={summary['fail_human']} fail_llm={summary['fail_llm']}")
        detail_path = _write_validation_detail(input_path, summary, reports)
        if detail_path:
            logger.info(f"校验详情: {detail_path}")
        human_path = _write_human_review_cases(input_path, spec=spec, spec_path=spec_path, reports=reports)
        if human_path:
            logger.info(f"人工处理清单: {human_path}")
        if args.emit_fix_prompts or fix_inputs:
            logger.info(f"已写入修复提示词输入: {len(fix_inputs)} 个 -> {fix_path}")
        if should_apply_llm and llm_results:
            logger.info(f"已写入修复结果: {len(llm_results)} 个 -> {results_path}")
        ai_payload = {
            "enabled": llm_enabled,
            "status": ai_status,
            "autofix_attempted": ai_autofix_attempted,
            "autofix_status": ai_autofix_status,
            "llm_rounds": llm_round,
            "fail_llm_remaining": summary.get("fail_llm"),
        }
        _write_ai_iteration_status(input_path, ai_payload)
        print(f"AI_ITERATION_STATUS={ai_status}")
        return

    if not input_path.exists():
        logger.error("input_path 不存在")
        sys.exit(2)

    pdf_path = None
    if input_path.name.endswith("_extracted_revised.json"):
        pdf_path = input_path.with_name(input_path.name.replace("_extracted_revised.json", ".pdf"))
    report = validate_extracted_json(
        input_path, spec, pdf_path=pdf_path, extracted_text_preview_len=args.preview_len
    )
    reports = _apply_company_consistency_checks(input_path.parent, [report], company_records)
    report = reports[0] if reports else report

    summary = _rebuild_summary(spec.get("spec_version"), input_path.parent, [report])

    should_apply_llm = bool(args.apply_llm or args.auto_apply_llm)
    if should_apply_llm and report.get("status") == "FAIL_LLM":
        max_llm_rounds = 2
        llm_round = 0
        stalled = False
        last_error_count = _report_error_count(report)

        fix_path = (
            Path(args.fix_prompts_path)
            if args.fix_prompts_path
            else input_path.with_name(input_path.name.replace("_extracted_revised.json", "_llm_fix_input.json"))
        )
        results_path = (
            Path(args.llm_results_path)
            if args.llm_results_path
            else input_path.with_name(input_path.name.replace("_extracted_revised.json", "_llm_fix_result.json"))
        )

        single_client: Any = None
        model = ""
        settings = _load_ark_settings(args.model)
        if not settings["api_key"]:
            logger.error("未检测到 ARK_API_KEY，无法自动 LLM 修复，已升级为人工处理")
            errors = report.get("errors")
            error_list = list(errors) if isinstance(errors, list) else []
            error_list.append(
                {
                    "code": "LLM_API_KEY_MISSING",
                    "field": "llm_fix",
                    "message": "未配置 ARK_API_KEY，无法自动 LLM 修复",
                }
            )
            report["errors"] = error_list
            report["status"] = "FAIL_HUMAN"
            should_apply_llm = False
        else:
            single_client = _create_ark_client(settings["base_url"], settings["api_key"])
            model = settings["model"]

        while should_apply_llm and report.get("status") == "FAIL_LLM" and llm_round < max_llm_rounds and not stalled:
            fix_input = build_fix_prompt_input(
                report,
                input_path.parent,
                spec,
                spec_path=spec_path,
                fix_text_len=args.fix_text_len,
            )
            if fix_input is None:
                break

            if args.emit_fix_prompts:
                _write_json(fix_path, fix_input)
                logger.info(f"修复提示词输入: {fix_path}")

            llm_round += 1
            logger.info(f"LLM 修复轮次: {llm_round}/{max_llm_rounds}")
            result = _apply_llm_fix(
                fix_input=fix_input,
                json_dir=input_path.parent,
                spec=spec,
                client=single_client,
                model=model,
                preview_len=args.preview_len,
            )
            if isinstance(result, dict):
                result["llm_round"] = llm_round
            _write_json(results_path, result)
            logger.info(f"修复结果: {results_path}")

            report = validate_extracted_json(input_path, spec, extracted_text_preview_len=args.preview_len)
            reports = _apply_company_consistency_checks(input_path.parent, [report], company_records)
            report = reports[0] if reports else report

            current_error_count = _report_error_count(report)
            if current_error_count >= last_error_count:
                stalled = True
            last_error_count = current_error_count

        if should_apply_llm and report.get("status") == "FAIL_LLM":
            try:
                result = run_autofix(
                    input_path,
                    spec_path=spec_path,
                    model=args.model,
                )
                logger.info(f"自动迭代结果: {result.get('status')}")
            except Exception:
                logger.exception("自动迭代失败")

        if report.get("status") == "FAIL_LLM":
            errors = report.get("errors")
            error_list = list(errors) if isinstance(errors, list) else []
            error_list.append(
                {
                    "code": "LLM_FIX_MAX_ROUNDS",
                    "field": "llm_fix",
                    "message": "LLM 修复超过最大轮次仍失败，已升级为人工处理",
                }
            )
            report["errors"] = error_list
            report["status"] = "FAIL_HUMAN"

        summary["pass"] = 1 if report.get("status") == "PASS" else 0
        summary["fail_human"] = 1 if report.get("status") == "FAIL_HUMAN" else 0
        summary["fail_llm"] = 1 if report.get("status") == "FAIL_LLM" else 0

    detail_path = _write_validation_detail(input_path.parent, summary, [report])
    if report.get("status") != "PASS" and detail_path:
        logger.info(f"FAIL 详情: {detail_path}")
    else:
        logger.info("校验通过")
    _write_human_review_cases(input_path.parent, spec=spec, spec_path=spec_path, reports=[report])


if __name__ == "__main__":
    main()
