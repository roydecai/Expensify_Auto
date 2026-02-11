import argparse
import json
import logging
import os
from pathlib import Path

from pdf_json_validator import load_spec, validate_dir, validate_extracted_json


def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _default_spec_path() -> Path:
    return Path(__file__).with_name("pdf_to_json_spec_v0_3_1.json")


def _report_path_for_json(json_path: Path) -> Path:
    if json_path.name.endswith("_extracted_revised.json"):
        return json_path.with_name(json_path.name.replace("_extracted_revised.json", "_validation_report.json"))
    return json_path.with_suffix(".validation_report.json")


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _truncate_text(text, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[:limit]


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
            "- 所有必须字段（除 tax_certificate.tax_authority）要求 non-empty（trim 后不为空）。",
            "- 如果出现名称空格问题：括号外不允许空白字符；括号内允许空格。",
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", help="单个 *_extracted_revised.json 文件或包含这些文件的目录")
    parser.add_argument("--spec", default=None, help="校验规则 spec 文件路径（默认使用 v0.3.1）")
    parser.add_argument("--preview-len", type=int, default=1000, help="extracted_text 预览截断长度")
    parser.add_argument("--emit-fix-prompts", action="store_true", help="为 FAIL_LLM 样本生成 LLM 修复提示词输入文件")
    parser.add_argument("--fix-prompts-path", default=None, help="LLM 修复提示词输入输出路径（默认写到目录内）")
    parser.add_argument("--fix-text-len", type=int, default=8000, help="LLM 修复输入中的 extracted_text 截断长度")
    args = parser.parse_args()

    configure_logging()
    logger = logging.getLogger(__name__)

    spec_path = Path(args.spec) if args.spec else _default_spec_path()
    spec = load_spec(spec_path)

    input_path = Path(args.input_path)
    if input_path.is_dir():
        summary, reports = validate_dir(input_path, spec, extracted_text_preview_len=args.preview_len)
        summary_path = input_path / "validation_summary.json"
        _write_json(summary_path, summary)

        written = []
        fix_inputs = []
        for report in reports:
            if report.get("status") == "PASS":
                continue
            json_filename = report.get("context", {}).get("json_filename")
            if not isinstance(json_filename, str):
                continue
            json_path = input_path / json_filename
            report_path = _report_path_for_json(json_path)
            _write_json(report_path, report)
            written.append(report_path.name)

            if args.emit_fix_prompts:
                fix_input = build_fix_prompt_input(
                    report,
                    input_path,
                    spec,
                    spec_path=spec_path,
                    fix_text_len=args.fix_text_len,
                )
                if fix_input is not None:
                    fix_inputs.append(fix_input)

        if args.emit_fix_prompts:
            fix_path = Path(args.fix_prompts_path) if args.fix_prompts_path else (input_path / "llm_fix_inputs.json")
            _write_json(fix_path, fix_inputs)

        logger.info(f"校验完成: total={summary['total']} pass={summary['pass']} fail_human={summary['fail_human']} fail_llm={summary['fail_llm']}")
        logger.info(f"Summary: {summary_path}")
        if written:
            logger.info(f"已写入失败报告: {len(written)} 个")
        if args.emit_fix_prompts:
            logger.info(f"已写入修复提示词输入: {len(fix_inputs)} 个 -> {fix_path}")
        return

    if not input_path.exists():
        logger.error("input_path 不存在")
        os.sys.exit(2)

    report = validate_extracted_json(input_path, spec, extracted_text_preview_len=args.preview_len)

    summary = {
        "spec_version": spec.get("spec_version"),
        "dir": str(input_path.parent),
        "total": 1,
        "pass": 1 if report.get("status") == "PASS" else 0,
        "fail_human": 1 if report.get("status") == "FAIL_HUMAN" else 0,
        "fail_llm": 1 if report.get("status") == "FAIL_LLM" else 0,
        "errors_by_code": {},
    }
    for err in report.get("errors", []):
        code = err.get("code")
        if isinstance(code, str):
            summary["errors_by_code"][code] = summary["errors_by_code"].get(code, 0) + 1

    summary_path = input_path.parent / "validation_summary.json"
    _write_json(summary_path, summary)

    if report.get("status") != "PASS":
        report_path = _report_path_for_json(input_path)
        _write_json(report_path, report)
        logger.info(f"FAIL 报告: {report_path}")

        if args.emit_fix_prompts:
            fix_input = build_fix_prompt_input(
                report,
                input_path.parent,
                spec,
                spec_path=spec_path,
                fix_text_len=args.fix_text_len,
            )
            if fix_input is not None:
                fix_path = (
                    Path(args.fix_prompts_path)
                    if args.fix_prompts_path
                    else input_path.with_name(input_path.name.replace("_extracted_revised.json", "_llm_fix_input.json"))
                )
                _write_json(fix_path, fix_input)
                logger.info(f"修复提示词输入: {fix_path}")
    logger.info(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
