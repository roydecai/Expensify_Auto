import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pdf_extraction_service import PDFExtractionService


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {}
    for key, value in payload.items():
        if key == "extracted_text":
            continue
        cleaned[key] = value
    return cleaned


def _compare_truth(output: Dict[str, Any], truth: Dict[str, Any]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    for key, truth_value in truth.items():
        output_value = output.get(key)
        if output_value != truth_value:
            diffs.append(
                {
                    "field": key,
                    "expected": truth_value,
                    "actual": output_value,
                }
            )
    return diffs


def run_regression(
    samples_dir: Path,
    output_dir: Optional[Path] = None,
    workers: int = 1,
) -> Dict[str, Any]:
    samples_dir = samples_dir.resolve()
    pdf_paths = sorted(samples_dir.glob("*.pdf"))
    if not pdf_paths:
        return {
            "status": "SKIPPED",
            "reason": "no_samples",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "cases": [],
        }
    output_dir = output_dir or (samples_dir / "regression_outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    service = PDFExtractionService()
    if workers <= 1:
        service.process_pdfs_sequentially(pdf_paths, output_dir=str(output_dir))
    else:
        service.process_pdfs_multithread(pdf_paths, max_workers=workers, output_dir=str(output_dir))
    service.close()
    cases: List[Dict[str, Any]] = []
    passed = 0
    failed = 0
    for pdf_path in pdf_paths:
        output_path = output_dir / f"{pdf_path.stem}_extracted_revised.json"
        truth_path = samples_dir / f"{pdf_path.stem}_truth.json"
        if not truth_path.exists():
            cases.append(
                {
                    "file": pdf_path.name,
                    "status": "SKIPPED",
                    "reason": "missing_truth",
                }
            )
            continue
        if not output_path.exists():
            cases.append(
                {
                    "file": pdf_path.name,
                    "status": "FAILED",
                    "reason": "missing_output",
                }
            )
            failed += 1
            continue
        with open(output_path, "r", encoding="utf-8") as f:
            output_payload = json.load(f)
        with open(truth_path, "r", encoding="utf-8") as f:
            truth_payload = json.load(f)
        normalized = _normalize_payload(output_payload)
        diffs = _compare_truth(normalized, truth_payload)
        if diffs:
            cases.append(
                {
                    "file": pdf_path.name,
                    "status": "FAILED",
                    "diffs": diffs,
                }
            )
            failed += 1
        else:
            cases.append(
                {
                    "file": pdf_path.name,
                    "status": "PASSED",
                }
            )
            passed += 1
    return {
        "status": "DONE",
        "total": len(pdf_paths),
        "passed": passed,
        "failed": failed,
        "cases": cases,
    }
