import json
import re
import logging
from typing import Any, Dict, Iterable, Optional, List, Tuple
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

try:
    from ocr_engine import OCREngine
except ImportError:
    sys.path.append(str(Path(__file__).parent))
    from ocr_engine import OCREngine

from config import get_project_root
from date_utils import normalize_date_string
from text_utils import clean_project_name, extract_reconcile_vat_num

logger = logging.getLogger(__name__)

class PDFExtractor:
    def __init__(
        self,
        ocr_engine: Optional[OCREngine] = None,
        patterns: Optional[Dict[str, Any]] = None,
        company_records: Optional[List[Dict[str, Optional[str]]]] = None,
    ) -> None:
        self.ocr_engine = ocr_engine or OCREngine()
        patterns = patterns or self.load_patterns()
        self.doc_patterns = patterns['doc_patterns']
        self.field_patterns = patterns['field_patterns']
        self.company_records = company_records or []
        self.company_names = self._build_company_names(self.company_records)

    def load_patterns(self) -> Dict[str, Any]:
        patterns_path = Path(__file__).with_name('patterns.json')
        with open(patterns_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def detect_document_type(self, text: str) -> str:
        """基于文本内容检测文档类型 - 增强版"""
        if re.search(r'税\s*收\s*完\s*税\s*证\s*明', text, re.IGNORECASE):
            return 'tax_certificate'
        if re.search(r'(付款|收款)', text):
            return 'bank_receipt'

        # 按优先级检查文档类型
        scores = {}

        for doc_type, patterns in self.doc_patterns.items():
            score = 0
            for pattern in patterns:
                # 计算匹配次数作为权重
                matches = re.findall(pattern, text, re.IGNORECASE)
                score += len(matches)
            scores[doc_type] = score

        # 返回得分最高的文档类型，如果没有匹配项则返回'unknown'
        max_type = max(scores, key=scores.get) if scores else 'unknown'
        max_score = scores[max_type] if max_type != 'unknown' else 0

        # 设置阈值，如果最高得分大于0，则认为是该类型
        if max_score > 0:
            return max_type
        else:
            return 'unknown'

    def _clean_text(self, text: str) -> str:
        """清理文本：去除前缀和无关字符"""
        if not text:
            return ""
        # 去除常见前缀
        text = re.sub(r'^(名称|纳税人识别号|统一社会信用代码)[:：]\s*', '', text)
        # 去除首尾空白和货币符号
        text = re.sub(r'^[￥¥,\s]+', '', text)
        text = re.sub(r'[￥¥,\s]+$', '', text)
        return text.strip()

    def _normalize_text(self, text: str) -> str:
        """
        标准化文本：
        1. 去除中文汉字之间的少量空格（修复某些PDF为了对齐而插入的空格）
           注意：只去除1-3个空格，保留较长的空格（通常是分栏分隔符）
        """
        # 匹配：汉字 + 1-3个空格 + 汉字，替换为 汉字+汉字
        # 使用 lookbehind (?<=...) 和 lookahead (?=...) 确保只删除中间的空格
        return re.sub(r'(?<=[\u4e00-\u9fff])\s{1,3}(?=[\u4e00-\u9fff])', '', text)

    def _build_company_names(self, company_records: List[Dict[str, Optional[str]]]) -> List[str]:
        names: List[str] = []
        for record in company_records:
            if not isinstance(record, dict):
                continue
            for key in ("full_name", "short_name", "eng_full_name", "eng_short_name"):
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    names.append(value.strip())
        return names

    def _is_english_name(self, name: str) -> bool:
        if not name:
            return False
        if re.search(r"[\u4e00-\u9fff]", name):
            return False
        if not re.search(r"[A-Za-z]", name):
            return False
        return re.fullmatch(r"[A-Za-z][A-Za-z\.\,\s]*[A-Za-z\.\,]", name.strip()) is not None

    def _looks_like_company_name(self, name: str) -> bool:
        if not name:
            return False
        if '公司' in name or '企业' in name or '有限公司' in name:
            return True
        return self._is_english_name(name) and len(name.strip()) > 2

    def _is_own_company(self, name: str) -> bool:
        if not name:
            return False
        if not self.company_names:
            return False
        return any(company in name for company in self.company_names)

    def _is_bank_name(self, name: str) -> bool:
        if not name:
            return False
        bank_keywords = ["银行", "分行", "支行", "信用社", "农村商业银行", "商业银行"]
        return any(keyword in name for keyword in bank_keywords)

    def _extract_counterparty_name(self, text: str) -> Optional[str]:
        patterns = [
            r'交易对方名称[:：]?\s*([^\n]{2,60}?)(?=\s+交易对方|\s+对方账号|\s+对方银行|\s+对方行号|$)',
            r'对方名称[:：]?\s*([^\n]{2,60}?)(?=\s+对方账号|\s+对方银行|\s+对方行号|$)',
            r'对方户名[:：]?\s*([^\n]{2,60}?)(?=\s+对方账号|\s+对方银行|\s+对方行号|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                result = self._clean_text(match.group(1).strip())
                if result:
                    return result
        return None

    def _trim_bank_receipt_name(self, value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return value
        trimmed = value
        cut_keywords = ["交易对方", "对方账号", "账号", "行号", "开户行", "银行名称", "银行行号"]
        for keyword in cut_keywords:
            idx = trimmed.find(keyword)
            if idx > 0:
                trimmed = trimmed[:idx]
        trimmed = trimmed.strip()
        return self._clean_text(trimmed) if trimmed else value

    def _detect_bank_receipt_direction(self, text: str, payer: Optional[str], payee: Optional[str]) -> Optional[str]:
        marker = re.search(r'借贷标志[:：]?\s*([借贷])', text)
        if marker:
            return "out" if marker.group(1) == "借" else "in"
        payer_is_own = isinstance(payer, str) and self._is_own_company(payer)
        payee_is_own = isinstance(payee, str) and self._is_own_company(payee)
        if payer_is_own and not payee_is_own:
            return "out"
        if payee_is_own and not payer_is_own:
            return "in"
        if "收款" in text and "付款" not in text:
            return "in"
        if "付款" in text and "收款" not in text:
            return "out"
        return None

    def _split_line(self, line: str) -> Tuple[str, str]:
        """将一行按大空格分割为左右两部分"""
        parts = re.split(r'\s{4,}', line.strip())
        if len(parts) >= 2:
            return parts[0], parts[-1] # 取首尾，假设中间是空的或者也是分隔符
        return line, ""

    def extract_field(self, text: str, patterns: List[str]) -> Optional[str]:
        """使用正则表达式模式提取字段 - 增强版"""
        for i, pattern in enumerate(patterns):
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                result = match.group(1).strip()
                logger.debug(f"Pattern {i} matched: {pattern} -> {result}")
                # 进一步验证结果的合理性
                if result and len(result) > 0 and not re.match(r'^[0-9\.\,\-\s]+$', result):
                    return self._clean_text(result)
        return None

    def extract_payer_from_invoice_layout(self, text: str) -> Optional[str]:
        """特别处理左右分栏的发票布局，提取付款方名称"""
        # 模式：购方名称 : [名称] 销方名称 : [名称]
        patterns = [
            r'购[^\n]*方[^\n]*名称[：:]\s*([^\n]{5,60})\s*销[^\n]*方[^\n]*名称[：:]\s*([^\n]{5,60})',
            r'购[^\n]*名[^\n]*称[：:]\s*([^\n]{5,60})\s*销[^\n]*名[^\n]*称[：:]\s*([^\n]{5,60})',
            r'名\s*称\s*([^\n]{5,60})\s*名\s*称\s*([^\n]{5,60})',  # 处理"名 称 公司A 名 称 公司B"这种格式
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match and len(match.groups()) >= 2:
                # 第一个通常是购买方(付款方)，第二个是销售方
                potential_payer = self._clean_text(match.group(1))
                # 验证是否是有效的公司名称
                if '公司' in potential_payer or '企业' in potential_payer or '有限公司' in potential_payer:
                    return potential_payer

        # 尝试处理竖排结构
        vertical_patterns = [
            r'购方[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
            r'购买方[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
            r'付款方[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
            r'付款单位[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
        ]

        for pattern in vertical_patterns:
            match = re.search(pattern, text)
            if match:
                result = self._clean_text(match.group(1))
                if '公司' in result or '企业' in result or '有限公司' in result:
                    return result

        return None

    def extract_seller_from_invoice_layout(self, text: str) -> Optional[str]:
        """特别处理左右分栏的发票布局，提取销售方名称"""
        # 模式：购方名称 : [名称] 销方名称 : [名称]
        patterns = [
            r'购[^\n]*方[^\n]*名称[：:]\s*([^\n]{5,60})\s*销[^\n]*方[^\n]*名称[：:]\s*([^\n]{5,60})',
            r'购[^\n]*名[^\n]*称[：:]\s*([^\n]{5,60})\s*销[^\n]*名[^\n]*称[：:]\s*([^\n]{5,60})',
            r'名\s*称\s*([^\n]{5,60})\s*名\s*称\s*([^\n]{5,60})',  # 处理"名 称 公司A 名 称 公司B"这种格式
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match and len(match.groups()) >= 2:
                # 第二个通常是销售方
                potential_seller = self._clean_text(match.group(2))
                # 验证是否是有效的公司名称
                if '公司' in potential_seller or '企业' in potential_seller or '有限公司' in potential_seller:
                    return potential_seller

        # 尝试处理竖排结构
        vertical_patterns = [
            r'销方[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
            r'销售方[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
            r'收款方[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
            r'收款单位[^\n]*\n[^\n]*[:：]\s*([^\n]{5,60})',
        ]

        for pattern in vertical_patterns:
            match = re.search(pattern, text)
            if match:
                result = self._clean_text(match.group(1))
                if '公司' in result or '企业' in result or '有限公司' in result:
                    return result

        return None

    def extract_amount_from_digital_format(self, text: str) -> Optional[str]:
        """特别处理数字格式的金额，如"¥83,108.84"或"83,108.84"这样的格式"""
        # 处理带符号的金额格式
        patterns = [
            r'[￥¥]\s*([0-9,，]+\.\d{2})',  # ¥83,108.84 格式
            r'[￥¥]\s*([0-9,，]+\.\d{2})',  # ￥83,108.84 格式
            r'([0-9,，]+\.\d{2})\s*[￥¥]',  # 83,108.84¥ 格式
            r'([0-9,，]+\.\d{2})',  # 纯数字格式
        ]

        amounts = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                clean_amount = re.sub(r'[,，]', '', match)  # 移除逗号
                try:
                    float_val = float(clean_amount)
                    if float_val > 0:  # 只考虑正数金额
                        amounts.append((float_val, clean_amount))
                except ValueError:
                    continue

        # 返回最大且合理的金额
        if amounts:
            # 过滤掉明显不合理的金额（如太小或太大）
            reasonable_amounts = [amt for amt in amounts if 0.01 <= amt[0] <= 100000000]
            if reasonable_amounts:
                max_amount = max(reasonable_amounts, key=lambda x: x[0])
                return f"{max_amount[0]:.2f}"

        return None

    def extract_multiple_amounts(self, text: str) -> List[float]:
        """提取多个金额并排序，取最大的几个作为候选"""
        amount_patterns = self.field_patterns['common']['amount']
        amounts = []

        for pattern in amount_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    clean_amount = re.sub(r'[￥¥,\s，]', '', match)
                    if clean_amount.replace('.', '').isdigit():
                        amounts.append(float(clean_amount))
                except ValueError:
                    continue

        # 返回去重后按大小排序的金额
        return sorted(list(set(amounts)), reverse=True)

    def extract_amount(self, text: str) -> Optional[str]:
        """提取金额 - 增强版，更准确地识别主要金额"""
        amounts = self.extract_multiple_amounts(text)

        if amounts:
            # 对于发票类文档，通常最大金额是总价
            # 对于其他文档，选择最合理的金额
            return f"{amounts[0]:.2f}"  # 返回最大金额
        return None

    def extract_tax_amount(self, text: str) -> Optional[str]:
        """提取税额 - 从发票文本中提取税额"""
        # 模式1：查找"合 计 ¥金额 ¥税额"格式
        pattern1 = r'合\s*计\s*[￥¥]?\s*([\d,，]+\.\d{2})\s*[￥¥]?\s*([\d,，]+\.\d{2})'
        match1 = re.search(pattern1, text)
        if match1:
            # 第二个数字是税额
            tax_amount = match1.group(2)
            clean_tax = re.sub(r'[￥¥,\s，]', '', tax_amount)
            try:
                return f"{float(clean_tax):.2f}"
            except ValueError:
                pass
        
        # 模式2：查找"税额"字段
        pattern2 = r'税\s*额\s*[：:]?\s*[￥¥]?\s*([\d,，]+\.\d{2})'
        match2 = re.search(pattern2, text)
        if match2:
            tax_amount = match2.group(1)
            clean_tax = re.sub(r'[￥¥,\s，]', '', tax_amount)
            try:
                return f"{float(clean_tax):.2f}"
            except ValueError:
                pass
        
        # 模式3：在表格行中查找税额
        # 查找包含"税率"和数字的行
        lines = text.split('\n')
        for line in lines:
            # 查找包含百分比和金额的行，如"13% 9.89"
            tax_match = re.search(r'\d+%\s+([\d,，]+\.\d{2})', line)
            if tax_match:
                tax_amount = tax_match.group(1)
                clean_tax = re.sub(r'[￥¥,\s，]', '', tax_amount)
                try:
                    return f"{float(clean_tax):.2f}"
                except ValueError:
                    continue
        
        return None

    def extract_date(self, text: str) -> Optional[str]:
        """提取日期 - 增强版，更好的日期格式化"""
        date_patterns = self.field_patterns['common']['date']
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1).strip()
                normalized = normalize_date_string(date_str)
                return normalized if isinstance(normalized, str) and normalized else date_str
        return None

    def extract_invoice_fields(self, text: str) -> Dict[str, Optional[str]]:
        """提取发票特有字段，专门处理左右分栏布局 - 增强版"""
        fields = {
            'seller': None,
            'seller_tax_id': None,
            'project_name': None,
            'payer': None, # 发票特定的付款人提取
            'buyer_tax_id': None
        }

        lines = text.split('\n')
        project_header_found = False

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # 1. 提取购买方和销售方名称 - 增强版
            # 模式：左边有"购"，右边有"销"
            if ('购' in line or '买方' in line) and ('销' in line or '卖方' in line or '销售方' in line) and '名称' in line:
                left, right = self._split_line(line)
                # 尝试从分割后的文本中提取名称
                if '购' in left or '买方' in left:
                    m = re.search(r'(?:购货)?(?:买方|方|单位|名称)[:：]?\s*([^\s]{2,30})', left)
                    if m:
                        fields['payer'] = m.group(1)
                if '销' in right or '卖方' in right or '销售方' in right:
                    m = re.search(r'销售方[:：]?\s*([^\s]{2,30})', right)
                    if m:
                        fields['seller'] = m.group(1)
            elif '销售方' in line and '名称' in line:
                m = re.search(r'销售方[:：]?\s*([^\n]{5,60})', line)
                if m:
                    fields['seller'] = self._clean_text(m.group(1))
            elif '购买方' in line and '名称' in line:
                m = re.search(r'购买方[:：]?\s*([^\n]{5,60})', line)
                if m:
                    fields['payer'] = self._clean_text(m.group(1))

            # 特殊处理左右分栏的购销名称行
            if '购' in line and '销' in line and ('名称' in line or '方' in line):
                parts = re.split(r'\s{2,}', line)  # 按多个空格分割
                for part in parts:
                    if '购' in part and '名称' in part:
                        buyer_match = re.search(r'购[^\n]*名称[：:]\s*([^\s]+)', part)
                        if buyer_match:
                            fields['payer'] = buyer_match.group(1)
                    if '销' in part and '名称' in part:
                        seller_match = re.search(r'销[^\n]*名称[：:]\s*([^\s]+)', part)
                        if seller_match:
                            fields['seller'] = seller_match.group(1)

            # 如果没有找到明确的分隔，尝试从含有"购"和"销"的行中提取信息
            if '购' in line and '销' in line:
                # 检查是否包含名称信息
                buyer_matches = re.findall(r'购[^\n]*名称[：:]\s*([^\s]+)', line)
                seller_matches = re.findall(r'销[^\n]*名称[：:]\s*([^\s]+)', line)

                if buyer_matches and not fields['payer']:
                    fields['payer'] = buyer_matches[0]
                if seller_matches and not fields['seller']:
                    fields['seller'] = seller_matches[0]

            # 2. 提取税号 - 增强版
            # 模式：包含"纳税人识别号"或"统一社会信用代码"
            if '纳税人识别号' in line or '统一社会信用代码' in line:
                # 提取所有看起来像税号的字符串 (15-20位字母数字)
                tax_ids = re.findall(r'[0-9A-Z]{15,20}', line)
                if len(tax_ids) >= 2:
                    # 假设顺序是买方税号、卖方税号（或反之）
                    # 根据上下文判断
                    if '购买方' in line or '买方' in line:
                        fields['buyer_tax_id'] = tax_ids[0]
                        if len(tax_ids) > 1:
                            fields['seller_tax_id'] = tax_ids[1]
                    elif '销售方' in line or '销方' in line:
                        fields['seller_tax_id'] = tax_ids[0]
                        if len(tax_ids) > 1:
                            fields['buyer_tax_id'] = tax_ids[1]
                    else:
                        # 默认分配，通常第一个是买方，第二个是卖方
                        fields['buyer_tax_id'] = tax_ids[0]
                        if len(tax_ids) > 1:
                            fields['seller_tax_id'] = tax_ids[1]
                elif len(tax_ids) == 1:
                    # 单个税号，根据上下文判断归属
                    if '销售方' in line or '销方' in line:
                        fields['seller_tax_id'] = tax_ids[0]
                    elif '购买方' in line or '买方' in line:
                        fields['buyer_tax_id'] = tax_ids[0]
                    else:
                        # 尝试从文本上下文判断
                        prev_lines = '\n'.join(lines[max(0, i-3):i])
                        if '销售方' in prev_lines or '销方' in prev_lines:
                            fields['seller_tax_id'] = tax_ids[0]
                        elif '购买方' in prev_lines or '买方' in prev_lines:
                            fields['buyer_tax_id'] = tax_ids[0]

            # 3. 提取项目名称 - 增强版
            # 策略：找到"项目名称"表头，取下一行非空内容
            if '项目名称' in line or '货物或应税劳务' in line or '服务名称' in line:
                project_header_found = True
                continue # 跳过表头行

            if project_header_found and not fields['project_name']:
                # 忽略纯数字或日期行
                if re.match(r'^[\d\s\-\.:]+$', line):
                    continue
                # 如果行以*开头，很可能是项目名称
                if line.startswith('*'):
                    # 提取两个*之间的内容，或者直接取整行（截断到数字）
                    if line.count('*') >= 2:
                        fields['project_name'] = clean_project_name(line)
                    else:
                        m = re.search(r'\*([^*]+)\*', line)
                        if m:
                            fields['project_name'] = clean_project_name(m.group(1))
                        else:
                            # 取第一个非数字非标点词汇
                            parts = re.findall(r'[\u4e00-\u9fff\w]+', line)
                            if parts:
                                fields['project_name'] = clean_project_name(parts[0])
                else:
                     # 查找中文词语，排除金额和数字
                     chinese_words = re.findall(r'[\u4e00-\u9fff\w]+', line)
                     # 过滤掉过于简短或可能是其他字段的词
                     valid_words = [word for word in chinese_words
                                   if len(word) > 1 and not re.match(r'^\d+\.?\d*$', word)]
                     if valid_words:
                         fields['project_name'] = clean_project_name(valid_words[0])

        # 如果循环结束后仍未找到，尝试使用通用正则作为兜底
        if not fields['seller']:
            m = re.search(r'(?:销\s*售\s*方|销方|销售单位)[:：]?\s*([^\n]{5,60})', text)
            if m:
                fields['seller'] = self._clean_text(m.group(1))

        # 再次尝试提取销售方和销售方税号，使用更多模式
        if not fields['seller']:
            # 查找销售方名称的各种可能格式
            seller_patterns = [
                r'(?:销售方|销方|销售单位)[:：]?\s*([^\n]{5,60})',
                r'销售方[^\n]*[:：]?\s*([^\n]{5,60})',
                r'销售方[^\n]*[:：]?\s*([^\s]{2,60})',
                r'名\s*称[^\n]*销[^\n]*([^\n]+)',  # 销售方名称格式
                r'销[^\n]*名\s*称[^\n]*([^\n]+)'   # 另格式
            ]
            for pattern in seller_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    fields['seller'] = self._clean_text(match.group(1))
                    break

        if not fields['seller_tax_id']:
            # 查找销售方税号的各种可能格式
            seller_tax_patterns = [
                r'(?:销售方|销方|销售单位)[^\n]*纳税人识别号[:：]?\s*([0-9A-Z]{15,20})',
                r'(?:销售方|销方|销售单位)[^\n]*统一社会信用代码[:：]?\s*([0-9A-Z]{15,20})',
                r'销售方[^\n]*(?:纳税人识别号|统一社会信用代码)[:：]?\s*([0-9A-Z]{15,20})',
                r'统一社会信用代码[:：]?\s*([0-9A-Z]{15,20}).*销售方'  # 有时顺序可能不同
            ]
            for pattern in seller_tax_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    fields['seller_tax_id'] = match.group(1)
                    break

        return fields

    def extract_common_fields(self, text: str) -> Dict[str, Optional[str]]:
        """提取通用字段 - 增强版"""
        common_patterns = self.field_patterns['common']

        return {
            'payer': self.extract_field(text, common_patterns['payer']),
            'amount': self.extract_amount(text),
            'uid': self.extract_field(text, common_patterns['uid']),
            'date': self.extract_date(text),
            'currency': self.extract_currency(text)
        }

    def extract_pdf_info(self, pdf_path: str) -> Dict[str, Any]:
        """主函数：提取PDF信息 - 增强版"""
        try:
            text = self.ocr_engine.extract_text(pdf_path)
            return self.extract_pdf_info_from_text(text)
        except Exception as e:
            logger.error(f"提取PDF信息时出错: {e}")
            return {'error': str(e)}

    def extract_currency(self, text: str) -> Optional[str]:
        if re.search(r'人民币|RMB|[￥¥]', text, re.IGNORECASE):
            return 'CNY'
        if re.search(r'CNY', text, re.IGNORECASE):
            return 'CNY'
        if re.search(r'美元|USD|\$', text, re.IGNORECASE):
            return 'USD'
        if re.search(r'港币|HKD', text, re.IGNORECASE):
            return 'HKD'
        if re.search(r'欧元|EUR', text, re.IGNORECASE):
            return 'EUR'
        return None

    def extract_pdf_info_from_text(self, text: str) -> Dict[str, Any]:
        try:
            if not text or len(text.strip()) < 10:
                return {'error': '无法从PDF中提取有效文本'}
            
            # 预处理：标准化文本，去除中文间空格
            text = self._normalize_text(text)
            
            doc_type = self.detect_document_type(text)
            cleaned_text = re.sub(r'[ \t]+', ' ', text)
            cleaned_text = re.sub(r'\n\s*\n', '\n', cleaned_text)
            result = {
                'document_type': doc_type,
                'extracted_text': cleaned_text[:1000] + '...' if len(cleaned_text) > 1000 else cleaned_text
            }
            common_fields = self.extract_common_fields(text)
            result.update(common_fields)
            if doc_type == 'invoice':
                is_red_invoice = re.search(r'红冲', text) is not None
                result['document_type'] = 'VAT_invalid_invoice' if is_red_invoice else 'VAT_invoice'
                
                # 提取税额
                tax_amount = self.extract_tax_amount(text)
                if tax_amount:
                    result['tax_amount'] = tax_amount
                
                # 将amount重命名为total_amount
                if 'amount' in result:
                    result['total_amount'] = result.pop('amount')
                
                invoice_fields = self.extract_invoice_fields(text)
                result['payer'] = result.get('payer') or invoice_fields.get('payer')
                result['seller'] = result.get('seller') or invoice_fields.get('seller')
                result['seller_tax_id'] = result.get('seller_tax_id') or invoice_fields.get('seller_tax_id')
                result['project_name'] = result.get('project_name') or invoice_fields.get('project_name')
                if result.get('payer') is None:
                    result['payer'] = self.extract_payer_from_invoice_layout(text)
                if result.get('seller') is None:
                    result['seller'] = self.extract_seller_from_invoice_layout(text)
                if result.get('uid') is None:
                    result['uid'] = common_fields.get('uid') or invoice_fields.get('uid')
                for key, value in invoice_fields.items():
                    if value is not None:
                        result[key] = value
                if result.get('project_name'):
                    result['project_name'] = clean_project_name(result.get('project_name'))
                if result.get('document_type') == 'VAT_invalid_invoice':
                    reconcile_vat_num = extract_reconcile_vat_num(text)
                    if reconcile_vat_num:
                        result['reconcile_VAT_num'] = reconcile_vat_num
                if result.get('seller') is None:
                    seller_pattern = r'销售方[^\n]*[:：]?\s*([^\n]{5,60})'
                    seller_match = re.search(seller_pattern, text)
                    if seller_match:
                        result['seller'] = self._clean_text(seller_match.group(1))
                if result.get('uid') is None:
                    uid_pattern = r'(?:发票号码|水单号|税票号码|UID|流水号|回单号|交易流水号|凭证号)[:：]?\s*([A-Za-z0-9]{8,20})'
                    uid_match = re.search(uid_pattern, text)
                    if uid_match:
                        result['uid'] = uid_match.group(1)
            elif doc_type == 'bank_receipt':
                bank_fields = self.extract_bank_receipt_fields(text)
                result.update(bank_fields)
                if not result.get('amount'):
                    small_amount_pattern = r'小写[:：]?\s*[￥¥]?\s*([0-9,，]+\.\d{2})|金额[:：]?\s*[￥¥]?\s*([0-9,，]+\.\d{2})'
                    small_amount_match = re.search(small_amount_pattern, text)
                    if small_amount_match:
                        for group in small_amount_match.groups():
                            if group:
                                clean_amount = re.sub(r'[￥¥,\s，]', '', group)
                                try:
                                    formatted_amount = f"{float(clean_amount):.2f}"
                                    result['amount'] = formatted_amount
                                    break
                                except ValueError:
                                    continue
                if not result.get('amount'):
                    result['amount'] = self.extract_amount_from_digital_format(text)
                if not result.get('uid'):
                    receipt_no_pattern = r'回单流水号[:：]?\s*([A-Za-z0-9]{8,30}[!$?]?)|交易流水号[:：]?\s*([A-Za-z0-9]{8,30}[!$?]?)|业务流水号[:：]?\s*([A-Za-z0-9]{8,30}[!$?]?)|回单编码[:：]?\s*([A-Za-z0-9]{8,30}[!$?]?)'
                    receipt_no_match = re.search(receipt_no_pattern, text)
                    if receipt_no_match:
                        for group in receipt_no_match.groups():
                            if group:
                                result['uid'] = group
                                break
            elif doc_type == 'tax_certificate':
                tax_fields = self.extract_tax_certificate_fields(text)
                result.update(tax_fields)
                if not result.get('amount'):
                    result['amount'] = self.extract_amount_from_digital_format(text)
                if not result.get('payer'):
                    taxpayer_pattern = r'(?:纳税人[:：]?\s*([^\n]{5,60})|缴款单位[:：]?\s*([^\n]{5,60})|纳税人名称[:：]?\s*([^\n]{5,60}))'
                    taxpayer_match = re.search(taxpayer_pattern, text)
                    if taxpayer_match:
                        for group in taxpayer_match.groups():
                            if group:
                                result['payer'] = self._clean_text(group)
                                break
            return result
        except Exception as e:
            logger.error(f"提取PDF信息时出错: {e}")
            return {'error': str(e)}

    def extract_bank_receipt_fields(self, text: str) -> Dict[str, Optional[str]]:
        """提取银行水单特有字段 - 增强版"""
        patterns = self.field_patterns['bank_receipt']
        fields = {
            'payee': self.extract_field(text, patterns['payee'])
        }

        # 针对广发银行水单特殊处理
        # 查找"名 称 北京磐沄科技有限公司 名 称 北京华诚邦友劳务服务有限公司"结构
        name_pattern = r'名\s*称\s*([^\n]*)\s*名\s*称\s*([^\n]*)'
        match = re.search(name_pattern, text)
        if match:
            # 第一个是付款方，第二个是收款方
            fields['payee'] = self._clean_text(match.group(2).strip())
            # 也需要设置付款方
            fields['payer'] = self._clean_text(match.group(1).strip())

        # 从通用模式中提取付款方
        if not fields.get('payer'):
            fields['payer'] = self.extract_field(text, self.field_patterns['common']['payer'])

        counterparty_name = self._extract_counterparty_name(text)
        if counterparty_name and (not fields.get('payee') or self._is_bank_name(fields.get('payee'))):
            fields['payee'] = counterparty_name

        if fields.get('payer'):
            fields['payer'] = self._trim_bank_receipt_name(fields.get('payer'))
        if fields.get('payee'):
            fields['payee'] = self._trim_bank_receipt_name(fields.get('payee'))

        # 如果没有提取到收款方，尝试其他模式
        if not fields['payee']:
            # 银行水单中收款方的其他可能表述
            alt_patterns = [
                r'收款单位[:：]?\s*([^\n]{5,60})',
                r'收款人[:：]?\s*([^\n]{5,60})',
                r'收.*方[:：]?\s*([^\n]{5,60})',
                r'对方户名[:：]?\s*([^\n]{5,60})',
                r'对方账号[:：]?\s*([^\n]{5,60})',
                r'名 称[^名]*收[^名]*([^\n]{5,60})'  # 匹配 "名 称" 后面的内容直到换行
            ]
            for pattern in alt_patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    result = match.group(1).strip()
                    if result and len(result) > 0:
                        fields['payee'] = self._clean_text(result)
                        break

        # 如果上面的方法都没找到收款方，尝试从"收"字附近提取
        if not fields['payee']:
            # 查找收方名称的特殊模式
            receipt_patterns = [
                r'付.*收\s*\n.*\n.*名\s*称\s*([^\n]+)\s*名\s*称\s*([^\n]+)',  # 多行结构
                r'名\s*称\s*[^\n]*收[^\n]*([^\n]+)',  # 名称后跟收款方
                r'收[^\n]*名\s*称[^\n]*([^\n]+)'  # 收...名称...模式
            ]
            for pattern in receipt_patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
                for match in matches:
                    # 检查哪个是收款方（通常是第二个名称）
                    if len(match.groups()) > 1:
                        result = match.group(2).strip()
                    else:
                        result = match.group(1).strip()

                    if result and len(result) > 0:
                        cleaned = self._clean_text(result)
                        if cleaned and self._looks_like_company_name(cleaned):
                            fields['payee'] = cleaned
                            break

        # 特别针对示例中的模式
        if not fields['payee']:
            # 查找类似 "名 称 北京磐沄科技有限公司 名 称 北京华诚邦友劳务服务有限公司" 的结构
            pattern = r'名\s*称\s*([^\n]*)\s*名\s*称\s*([^\n]*)'
            match = re.search(pattern, text)
            if match:
                # 第一个是付款方，第二个是收款方
                fields['payee'] = self._clean_text(match.group(2).strip())
                if not fields.get('payer'):
                    fields['payer'] = self._clean_text(match.group(1).strip())

        # 特别处理竖排名称的情况：付款方可能在垂直方向上
        # 查找可能的付款人信息
        if not fields.get('payer'):
            # 检查是否存在竖排的名称格式
            vertical_name_patterns = [
                r'付.*\s*\n\s*名\s*\n\s*([^\n]+)',
                r'付款.*\s*\n\s*名\s*\n\s*([^\n]+)',
                r'付款方.*\s*\n\s*名\s*\n\s*([^\n]+)',
                r'名\s*\n\s*([^\n]+)\s*\n\s*名\s*\n\s*([^\n]+)',  # 处理连续两行名称的情况
            ]
            for pattern in vertical_name_patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    # 检查是否有多个组匹配
                    groups = match.groups()
                    if len(groups) > 1:
                        # 第一组是第一行的名称，第二组是第二行的名称
                        first_name = self._clean_text(groups[0].strip())
                        second_name = self._clean_text(groups[1].strip())

                        # 一般情况下第一行是付款方，第二行是收款方
                        if self._looks_like_company_name(first_name):
                            fields['payer'] = first_name
                        if self._looks_like_company_name(second_name):
                            fields['payee'] = second_name
                    else:
                        # 只有一组匹配
                        name = self._clean_text(match.group(1).strip())
                        if self._looks_like_company_name(name):
                            fields['payer'] = name
                    break

        direction = self._detect_bank_receipt_direction(text, fields.get('payer'), fields.get('payee'))
        if direction:
            fields['direction'] = direction

        return fields

    def extract_tax_certificate_fields(self, text: str) -> Dict[str, Optional[str]]:
        """提取完税证明特有字段 - 增强版"""
        fields: Dict[str, Optional[str]] = {}

        # 提取纳税人名称（作为payer）
        # 注意：通用字段提取已经涵盖了纳税人名称的提取，且逻辑更完善
        # 这里仅作为补充，但要避免覆盖已经提取到的正确结果
        if not fields.get('payer'):
             # 使用修正后的模式，避免匹配到"纳税人识别号"
             taxpayer_pattern = r'纳税人(?!识别号|名称)[:：]?\s*([^\n]{5,60})|缴款单位[:：]?\s*([^\n]{5,60})|纳税人名称[:：]?\s*([^\n]{5,60})'
             taxpayer_match = re.search(taxpayer_pattern, text)
             if taxpayer_match:
                 # 取第一个非空的组
                 for group in taxpayer_match.groups():
                     if group:
                         fields['payer'] = self._clean_text(group)
                         break

        # 提取纳税人识别号（作为payer_tax_id）
        taxpayer_id_pattern = r'纳税人识别号[:：]?\s*([0-9A-Z]{15,20})|统一社会信用代码[:：]?\s*([0-9A-Z]{15,20})'
        taxpayer_id_match = re.search(taxpayer_id_pattern, text)
        if taxpayer_id_match:
            # 取第一个非空的组
            for group in taxpayer_id_match.groups():
                if group:
                    fields['payer_tax_id'] = group
                    break

        # 提取金额合计，格式如 "¥83,108.84"
        amount_total_pattern = r'金额合计[：:]?\s*[￥¥]?\s*([0-9,，]+\.\d{2})'
        amount_match = re.search(amount_total_pattern, text)
        if amount_match:
            raw_amount = re.sub(r'[￥¥,\s，]', '', amount_match.group(1))
            try:
                formatted_amount = f"{float(raw_amount):.2f}"
                fields['amount'] = formatted_amount
            except ValueError:
                pass  # 如果转换失败，不设置amount字段

        # 如果没有找到金额合计，尝试其他金额模式
        if not fields.get('amount'):
            # 查找带¥符号的金额
            yu_pattern = r'[￥¥]\s*([0-9,，]+\.\d{2})'
            yu_matches = re.findall(yu_pattern, text)
            if yu_matches:
                # 取第一个或最大的金额
                for match in yu_matches:
                    clean_amount = re.sub(r'[,，]', '', match)
                    try:
                        formatted_amount = f"{float(clean_amount):.2f}"
                        fields['amount'] = formatted_amount
                        break
                    except ValueError:
                        continue

        # 提取No后面的数字作为uid
        no_pattern = r'No\s*[：:]?\s*([A-Za-z0-9]{8,20})'
        no_match = re.search(no_pattern, text)
        if no_match:
            fields['uid'] = no_match.group(1)

        # 如果没有找到No开头的uid，尝试其他uid模式
        if not fields.get('uid'):
            # 也尝试原始的uid模式
            fields['uid'] = self.extract_field(text, self.field_patterns['common']['uid'])

        return fields

def process_single_pdf(
    pdf_path: Path, output_dir: Optional[str] = None, extractor: Optional[PDFExtractor] = None
) -> Tuple[str, Dict[str, Any]]:
    """处理单个PDF文件的辅助函数"""
    try:
        extractor = extractor or PDFExtractor()
        result = extractor.extract_pdf_info(str(pdf_path))
        # 修改：默认输出目录改为项目根目录下的temp目录
        if output_dir is None:
            # 获取项目根目录并创建temp目录
            output_dir = get_project_root() / "temp"
        else:
            output_dir = Path(output_dir)
        
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{Path(pdf_path).stem}_extracted_revised.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return Path(pdf_path).name, result
    except Exception as e:
        logger.error(f"处理文件 {pdf_path} 时出错: {e}")
        result = {'error': str(e), 'file_path': str(pdf_path)}
        return Path(pdf_path).name, result


def process_pdfs_multithread(
    pdf_paths: Iterable[Path],
    max_workers: int = 4,
    output_dir: Optional[str] = None,
    extractor: Optional[PDFExtractor] = None,
) -> Dict[str, Dict[str, Any]]:
    """多线程处理多个PDF文件"""
    results: Dict[str, Dict[str, Any]] = {}
    extractor = extractor or PDFExtractor()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(process_single_pdf, path, output_dir, extractor): path
            for path in pdf_paths
        }

        for future in as_completed(future_to_path):
            filename, result = future.result()
            results[filename] = result
            print(f"完成处理: {filename}")

    return results


def process_pdfs_sequentially(pdf_paths: Iterable[Path], output_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """顺序处理多个PDF文件（备用方案）"""
    results: Dict[str, Dict[str, Any]] = {}
    extractor = PDFExtractor()

    # 修改：确定输出目录
    if output_dir is None:
        # 获取项目根目录并创建temp目录
        target_dir = get_project_root() / "temp"
    else:
        target_dir = Path(output_dir)
    
    target_dir.mkdir(parents=True, exist_ok=True)

    for pdf_path in pdf_paths:
        try:
            result = extractor.extract_pdf_info(str(pdf_path))
            output_file = target_dir / f"{Path(pdf_path).stem}_extracted_revised.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            results[pdf_path.name] = result
            print(f"完成处理: {pdf_path.name}")
        except Exception as e:
            logger.error(f"处理文件 {pdf_path} 时出错: {e}")
            result = {'error': str(e), 'file_path': str(pdf_path)}
            results[pdf_path.name] = result

    return results


def main() -> None:
    if len(sys.argv) == 1:
        test_file = "test.pdf"
        if os.path.exists(test_file):
            extractor = PDFExtractor()
            print(json.dumps(extractor.extract_pdf_info(test_file), ensure_ascii=False, indent=2))
        return
    import argparse
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
        logger.error("未找到PDF文件")
        return
    if args.sequential or args.workers <= 1:
        process_pdfs_sequentially(pdf_paths, output_dir=args.output_dir)
    else:
        process_pdfs_multithread(pdf_paths, max_workers=args.workers, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
