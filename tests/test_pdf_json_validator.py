import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from pdf_json_validator import load_spec, validate_dir, validate_extracted_json
from pdf_validation_service import build_fix_prompt_input


class TestPdfJsonValidator(unittest.TestCase):
    def setUp(self):
        self.spec_path = SRC_DIR / "pdf_to_json_spec_v0_3_1.json"
        self.spec = load_spec(self.spec_path)
        self.today = date(2026, 2, 11)

    def _write_json(self, path: Path, payload):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def test_vat_invoice_pass(self):
        payload = {
            "document_type": "VAT_invoice",
            "extracted_text": "发票号码：26312000000713810086 开票日期：2026年02月05日",
            "payer": "北京磐沄科技有限公司",
            "seller": "上海嘉静门诊部有限公司",
            "buyer_tax_id": "91110108MA01TE5L85",
            "seller_tax_id": "91310000MA1FL0E9X9",
            "project_name": "医疗服务",
            "date": "2026-02-05",
            "currency": "CNY",
            "uid": "26312000000713810086",
            "total_amount": "388.00",
            "tax_amount": "0.00"
        }
        with TemporaryDirectory() as td:
            p = Path(td) / "a_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertEqual(report["status"], "PASS")

    def test_unknown_is_fail_human(self):
        payload = {"document_type": "unknown", "extracted_text": "x"}
        with TemporaryDirectory() as td:
            p = Path(td) / "u_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertEqual(report["status"], "FAIL_HUMAN")

    def test_extracted_text_blank_is_fail_human(self):
        payload = {"document_type": "VAT_invoice", "extracted_text": "   \n"}
        with TemporaryDirectory() as td:
            p = Path(td) / "t_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertEqual(report["status"], "FAIL_HUMAN")

    def test_name_whitespace_outside_parentheses_is_fail_llm(self):
        payload = {
            "document_type": "bank_receipt",
            "extracted_text": "客户回单",
            "payer": "北京 磐沄科技有限公司",
            "payee": "收款方（上海 分部）",
            "date": "2026-02-05",
            "currency": "CNY",
            "uid": "ABCDEFGH12345678",
            "amount": "1.00"
        }
        with TemporaryDirectory() as td:
            p = Path(td) / "b_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertEqual(report["status"], "FAIL_LLM")
            codes = [e["code"] for e in report["errors"]]
            self.assertIn("NAME_HAS_WHITESPACE_OUTSIDE_BRACKETS", codes)

    def test_tax_id_chinese_name_requires_18(self):
        payload = {
            "document_type": "VAT_invoice",
            "extracted_text": "发票",
            "payer": "北京磐沄科技有限公司",
            "seller": "上海嘉静门诊部有限公司",
            "buyer_tax_id": "123456789012345",
            "seller_tax_id": "91310000MA1FL0E9X9",
            "project_name": "医疗服务",
            "date": "2026-02-05",
            "currency": "CNY",
            "uid": "26312000000713810086",
            "total_amount": "388.00",
            "tax_amount": "0.00"
        }
        with TemporaryDirectory() as td:
            p = Path(td) / "c_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertEqual(report["status"], "FAIL_LLM")
            codes = [e["code"] for e in report["errors"]]
            self.assertIn("TAX_ID_CJK_NAME_MUST_BE_18", codes)

    def test_tax_id_latin_name_allows_8_to_20(self):
        payload = {
            "document_type": "VAT_invoice",
            "extracted_text": "invoice",
            "payer": "ABCInc",
            "seller": "XYZLtd",
            "buyer_tax_id": "12345678",
            "seller_tax_id": "87654321",
            "project_name": "Service",
            "date": "2026-02-05",
            "currency": "USD",
            "uid": "ABCDEFGH12345678",
            "total_amount": "388.00",
            "tax_amount": "0.00"
        }
        with TemporaryDirectory() as td:
            p = Path(td) / "d_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertNotEqual(report["status"], "FAIL_HUMAN")
            codes = [e["code"] for e in report["errors"]]
            self.assertNotIn("TAX_ID_CJK_NAME_MUST_BE_18", codes)

    def test_validate_dir_summary(self):
        payload_pass = {
            "document_type": "bank_receipt",
            "extracted_text": "客户回单",
            "payer": "北京磐沄科技有限公司",
            "payee": "上海嘉静门诊部有限公司",
            "date": "2026-02-05",
            "currency": "CNY",
            "uid": "ABCDEFGH12345678",
            "amount": "1.00"
        }
        payload_fail = {
            "document_type": "bank_receipt",
            "extracted_text": "客户回单",
            "payer": "北京 磐沄科技有限公司",
            "payee": "上海嘉静门诊部有限公司",
            "date": "2026-02-05",
            "currency": "CNY",
            "uid": "ABCDEFGH12345678",
            "amount": "1.00"
        }
        with TemporaryDirectory() as td:
            td_path = Path(td)
            self._write_json(td_path / "p_extracted_revised.json", payload_pass)
            self._write_json(td_path / "f_extracted_revised.json", payload_fail)
            summary, reports = validate_dir(td_path, self.spec, today=self.today)
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["pass"], 1)
            self.assertEqual(summary["fail_llm"], 1)
            self.assertEqual(len(reports), 2)

    def test_build_fix_prompt_input_truncates_text_and_includes_error_codes(self):
        payload = {
            "document_type": "bank_receipt",
            "extracted_text": "X" * 200,
            "payer": "北京 磐沄科技有限公司",
            "payee": "上海嘉静门诊部有限公司",
            "date": "2026-02-05",
            "currency": "",
            "uid": "",
            "amount": "1.00",
        }
        with TemporaryDirectory() as td:
            td_path = Path(td)
            p = td_path / "b_extracted_revised.json"
            self._write_json(p, payload)
            report = validate_extracted_json(p, self.spec, today=self.today)
            self.assertEqual(report["status"], "FAIL_LLM")

            fix_input = build_fix_prompt_input(
                report,
                td_path,
                self.spec,
                spec_path=self.spec_path,
                fix_text_len=50,
            )
            self.assertIsNotNone(fix_input)
            messages = fix_input["messages"]
            self.assertEqual(len(messages), 2)
            user_content = messages[1]["content"]
            self.assertIn("REQUIRED_FIELD_EMPTY", user_content)
            self.assertIn("X" * 50, user_content)
            self.assertNotIn("X" * 51, user_content)


if __name__ == "__main__":
    unittest.main()
