import argparse
import gc
import json
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from config import get_company_db_path, get_project_root
from ocr_engine import OCREngine
from pdf_extractor import PDFExtractor


def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class PDFExtractionService:
    def __init__(self, use_gpu: bool = False, log_level: int = logging.INFO) -> None:
        configure_logging(log_level)
        self.logger = logging.getLogger(__name__)
        self.ocr_engine = OCREngine(use_gpu=use_gpu)
        self.patterns = self._load_patterns()
        self.company_records = self._load_company_records()
        self.extractor = PDFExtractor(
            ocr_engine=self.ocr_engine,
            patterns=self.patterns,
            company_records=self.company_records,
        )

    def _load_patterns(self) -> Dict[str, Any]:
        patterns_path = Path(__file__).with_name("patterns.json")
        with open(patterns_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _resolve_output_dir(self, output_dir: Optional[str]) -> Path:
        if output_dir is None:
            return get_project_root() / "temp"
        return Path(output_dir)

    def _load_company_records(self) -> List[Dict[str, Optional[str]]]:
        db_path = get_company_db_path()
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
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

    def process_pdf(self, pdf_path: Union[str, Path], output_dir: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        pdf_path = Path(pdf_path)
        target_dir = self._resolve_output_dir(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.logger.info(f"开始处理: {pdf_path.name}")
            result = self.extractor.extract_pdf_info(str(pdf_path))
            output_file = target_dir / f"{pdf_path.stem}_extracted_revised.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            self.logger.info(f"完成处理: {pdf_path.name}")
            return pdf_path.name, result
        except Exception as e:
            self.logger.error(f"处理文件 {pdf_path} 时出错: {e}")
            result = {"error": str(e), "file_path": str(pdf_path)}
            return pdf_path.name, result
        finally:
            gc.collect()

    def process_pdfs_multithread(
        self,
        pdf_paths: Iterable[Path],
        max_workers: int = 4,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(self.process_pdf, path, output_dir): path
                for path in pdf_paths
            }
            for future in as_completed(future_to_path):
                filename, result = future.result()
                results[filename] = result
        return results

    def process_pdfs_sequentially(
        self, pdf_paths: Iterable[Path], output_dir: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for pdf_path in pdf_paths:
            filename, result = self.process_pdf(pdf_path, output_dir)
            results[filename] = result
        return results

    def close(self) -> None:
        self.ocr_engine.release()
        gc.collect()


def main():
    if len(os.sys.argv) == 1:
        test_file = "test.pdf"
        if os.path.exists(test_file):
            service = PDFExtractionService()
            _, result = service.process_pdf(test_file)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            service.close()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sequential", action="store_true")
    args = parser.parse_args()
    input_path = Path(args.input_path)
    if input_path.is_dir():
        pdf_paths = sorted(input_path.glob("*.pdf"))
    else:
        pdf_paths = [input_path]
    if not pdf_paths:
        logging.getLogger(__name__).error("未找到PDF文件")
        return
    service = PDFExtractionService()
    if args.sequential or args.workers <= 1:
        service.process_pdfs_sequentially(pdf_paths, output_dir=args.output_dir)
    else:
        service.process_pdfs_multithread(pdf_paths, max_workers=args.workers, output_dir=args.output_dir)
    service.close()


if __name__ == "__main__":
    main()
