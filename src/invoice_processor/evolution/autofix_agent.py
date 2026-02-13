import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from config import get_default_model_id, get_project_root
from main import _create_ark_client, _extract_json_from_response, _load_ark_settings
from pdf_extraction_service import PDFExtractionService
from pdf_json_validator import load_spec, validate_dir

from evolution.pattern_mutator import PatternMutator
from evolution.regression_runner import run_regression
from evolution.safe_executor import SafeFileExecutor


def _load_mutations(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError("Mutations payload must be a list")
    return payload


def _parse_json_any(text: Any) -> Any:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = raw[start : end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        snippet = raw[start : end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


def _extract_mutations_from_response(resp: Any) -> List[Dict[str, Any]]:
    parsed = _extract_json_from_response(resp)
    if isinstance(parsed, dict):
        maybe = parsed.get("mutations")
        if isinstance(maybe, list):
            return [item for item in maybe if isinstance(item, dict)]
        return []
    text = str(resp) if resp is not None else ""
    parsed_any = _parse_json_any(text)
    if isinstance(parsed_any, list):
        return [item for item in parsed_any if isinstance(item, dict)]
    if isinstance(parsed_any, dict):
        maybe = parsed_any.get("mutations")
        if isinstance(maybe, list):
            return [item for item in maybe if isinstance(item, dict)]
    return []


def _resolve_json_dir(input_path: Path, output_dir: Path) -> Path:
    if input_path.is_dir():
        json_files = list(input_path.glob("*_extracted_revised.json"))
        if json_files:
            return input_path
        pdfs = sorted(input_path.glob("*.pdf"))
        if pdfs:
            service = PDFExtractionService()
            service.process_pdfs_sequentially(pdfs, output_dir=str(output_dir))
            service.close()
            return output_dir
    if input_path.is_file():
        if input_path.suffix.lower() == ".pdf":
            service = PDFExtractionService()
            service.process_pdf(input_path, output_dir=str(output_dir))
            service.close()
            return output_dir
        if input_path.name.endswith("_extracted_revised.json"):
            return input_path.parent
    raise ValueError(f"Unsupported input path: {input_path}")


def _is_improved(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    before_fail_llm = before.get("fail_llm", 0)
    before_pass = before.get("pass", 0)
    after_fail_llm = after.get("fail_llm", 0)
    after_pass = after.get("pass", 0)
    return after_fail_llm < before_fail_llm or after_pass > before_pass


def _run_quality_checks(project_root: Path) -> bool:
    commands = [
        [sys.executable, "-m", "ruff", "check", "src"],
        [
            sys.executable,
            "-m",
            "mypy",
            "src/invoice_processor/evolution",
            "--ignore-missing-imports",
            "--follow-imports=skip",
        ],
    ]
    for cmd in commands:
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode != 0:
            return False
    return True


def _build_llm_messages(
    reports: List[Dict[str, Any]],
    *,
    max_cases: int,
    preview_len: int,
) -> List[Dict[str, str]]:
    failed = []
    for report in reports:
        if report.get("status") in ("FAIL_LLM", "FAIL_HUMAN"):
            failed.append(report)
    cases = []
    for report in failed[:max_cases]:
        ctx = report.get("context", {})
        preview = ctx.get("extracted_text_preview") if isinstance(ctx, dict) else ""
        if isinstance(preview, str):
            preview = preview[:preview_len]
        else:
            preview = ""
        cases.append(
            {
                "document_type": report.get("document_type"),
                "errors": report.get("errors", []),
                "extracted_text_preview": preview,
            }
        )
    payload = {
        "task": "生成用于 patterns.json 的 field_patterns_overrides 规则追加",
        "output_schema": [
            {
                "action": "append_override_regex",
                "doc_type": "common",
                "field": "payer",
                "id": "unique_id",
                "priority": 500,
                "regex": "regex_here",
            }
        ],
        "notes": [
            "仅输出 JSON 数组",
            "每条规则只做字段抽取正则补充",
            "不要输出解释文本",
            "优先覆盖 common 字段，必要时可用 bank_receipt",
        ],
        "cases": cases,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": "你是发票抽取规则修正助手。"},
        {"role": "user", "content": content},
    ]


def _generate_llm_mutations(
    reports: List[Dict[str, Any]],
    *,
    model: str,
    max_cases: int,
    max_mutations: int,
    preview_len: int,
) -> List[Dict[str, Any]]:
    settings = _load_ark_settings(model)
    if not settings.get("api_key"):
        return []
    client = _create_ark_client(settings["base_url"], settings["api_key"])
    messages = _build_llm_messages(reports, max_cases=max_cases, preview_len=preview_len)
    response = client.responses.create(model=settings["model"], input=messages)
    mutations = _extract_mutations_from_response(response)
    unique = []
    seen = set()
    for item in mutations:
        rule_id = item.get("id")
        if isinstance(rule_id, str) and rule_id not in seen:
            seen.add(rule_id)
            unique.append(item)
    return unique[:max_mutations]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("--mutations-path", default=None)
    parser.add_argument("--samples-dir", default=None)
    parser.add_argument("--spec-path", default=None)
    parser.add_argument("--patterns-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--model", default=get_default_model_id())
    parser.add_argument("--llm-max-cases", type=int, default=3)
    parser.add_argument("--llm-max-mutations", type=int, default=3)
    parser.add_argument("--llm-preview-len", type=int, default=800)
    parser.add_argument("--use-llm", dest="use_llm", action="store_true", default=True)
    parser.add_argument("--no-llm", dest="use_llm", action="store_false")
    parser.add_argument("--run-quality-checks", dest="run_quality_checks", action="store_true", default=True)
    parser.add_argument("--no-quality-checks", dest="run_quality_checks", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_root = get_project_root()
    input_path = Path(args.input_path)
    output_dir = Path(args.output_dir) if args.output_dir else (project_root / "temp" / "autofix")
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = (
        Path(args.spec_path)
        if args.spec_path
        else project_root / "src" / "invoice_processor" / "pdf_to_json_spec_v0_3_1.json"
    )
    patterns_path = (
        Path(args.patterns_path)
        if args.patterns_path
        else project_root / "src" / "invoice_processor" / "patterns.json"
    )
    mutations_path = Path(args.mutations_path) if args.mutations_path else None

    spec = load_spec(spec_path)
    json_dir = _resolve_json_dir(input_path, output_dir)
    base_summary, base_reports = validate_dir(json_dir, spec)

    mutator = PatternMutator(patterns_path)
    executor = SafeFileExecutor(allowed_roots=[patterns_path.parent])

    for idx in range(args.max_rounds):
        run_id = executor.create_run_id()
        mutations: List[Dict[str, Any]] = []
        if mutations_path:
            mutations.extend(_load_mutations(mutations_path))
        if args.use_llm:
            mutations.extend(
                _generate_llm_mutations(
                    base_reports,
                    model=args.model,
                    max_cases=args.llm_max_cases,
                    max_mutations=args.llm_max_mutations,
                    preview_len=args.llm_preview_len,
                )
            )
        if not mutations:
            print("未生成任何规则变更")
            break
        patterns = mutator.load()
        for mutation in mutations:
            patterns = mutator.apply_mutation(patterns, mutation)
        executor.apply_json(patterns_path, patterns, run_id)
        if args.run_quality_checks:
            if not _run_quality_checks(project_root):
                executor.restore(run_id)
                print("质量检查失败，已回滚")
                break
        regression = None
        if args.samples_dir:
            regression = run_regression(Path(args.samples_dir), workers=1)
            if regression.get("failed", 0) > 0:
                executor.restore(run_id)
                print("回归失败，已回滚")
                break
        next_summary, next_reports = validate_dir(json_dir, spec)
        improved = _is_improved(base_summary, next_summary)
        if args.dry_run or not improved:
            executor.restore(run_id)
            status = "干跑回滚" if args.dry_run else "效果未提升，已回滚"
            print(status)
            break
        base_summary = next_summary
        base_reports = next_reports
        if idx == args.max_rounds - 1:
            print("修正完成")


if __name__ == "__main__":
    main()
