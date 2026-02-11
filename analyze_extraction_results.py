import json
import os
from pathlib import Path
from collections import defaultdict

def analyze_extraction_results(test_sample_dir):
    """分析PDF提取结果"""
    test_sample_path = Path(test_sample_dir)
    json_files = list(test_sample_path.glob("*_extracted_revised.json"))
    
    print(f"找到 {len(json_files)} 个JSON文件")
    
    # 统计信息
    stats = {
        'total_files': len(json_files),
        'by_document_type': defaultdict(int),
        'extraction_quality': {
            'excellent': 0,  # 所有关键字段都提取成功
            'good': 0,       # 大部分关键字段提取成功
            'fair': 0,       # 部分关键字段提取成功
            'poor': 0,       # 很少或没有关键字段提取成功
            'error': 0       # 提取过程中出现错误
        },
        'field_extraction_rates': defaultdict(lambda: {'extracted': 0, 'total': 0}),
        'document_type_accuracy': 0
    }
    
    # 关键字段定义
    key_fields_by_type = {
        'VAT_invoice': ['document_type', 'payer', 'seller', 'total_amount', 'date', 'uid', 'seller_tax_id'],
        'bank_receipt': ['document_type', 'payer', 'payee', 'amount', 'date', 'uid'],
        'tax_certificate': ['document_type', 'payer', 'amount', 'date', 'uid', 'tax_authority']
    }
    
    results = []
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 获取文件名
            filename = json_file.stem.replace('_extracted_revised', '')
            
            # 统计文档类型
            doc_type = data.get('document_type', 'unknown')
            stats['by_document_type'][doc_type] += 1
            
            # 检查文档类型是否正确（从文件名推断）
            expected_type = infer_document_type_from_filename(filename)
            if expected_type and doc_type == expected_type:
                stats['document_type_accuracy'] += 1
            
            # 评估提取质量
            quality = evaluate_extraction_quality(data, doc_type, key_fields_by_type)
            stats['extraction_quality'][quality] += 1
            
            # 统计字段提取率
            for field in data.keys():
                if field not in ['extracted_text', 'error']:
                    stats['field_extraction_rates'][field]['extracted'] += 1
                    stats['field_extraction_rates'][field]['total'] += 1
            
            # 记录结果
            result = {
                'filename': filename,
                'document_type': doc_type,
                'expected_type': expected_type,
                'quality': quality,
                'extracted_fields': {k: v for k, v in data.items() if k not in ['extracted_text', 'error']},
                'has_error': 'error' in data
            }
            results.append(result)
            
        except Exception as e:
            print(f"分析文件 {json_file} 时出错: {e}")
            stats['extraction_quality']['error'] += 1
    
    # 计算百分比
    stats['document_type_accuracy_rate'] = (stats['document_type_accuracy'] / stats['total_files']) * 100 if stats['total_files'] > 0 else 0
    
    # 计算字段提取率
    for field in stats['field_extraction_rates']:
        extracted = stats['field_extraction_rates'][field]['extracted']
        total = stats['field_extraction_rates'][field]['total']
        stats['field_extraction_rates'][field]['rate'] = (extracted / total * 100) if total > 0 else 0
    
    return stats, results

def infer_document_type_from_filename(filename):
    """从文件名推断文档类型"""
    filename_lower = filename.lower()
    
    if 'invinnr' in filename_lower or '发票' in filename_lower:
        return 'VAT_invoice'
    elif 'bankbll' in filename_lower or '水单' in filename_lower:
        return 'bank_receipt'
    elif 'taxpymt' in filename_lower or '完税' in filename_lower or '税票' in filename_lower:
        return 'tax_certificate'
    else:
        return None

def evaluate_extraction_quality(data, doc_type, key_fields_by_type):
    """评估提取质量"""
    if 'error' in data:
        return 'error'
    
    key_fields = key_fields_by_type.get(doc_type, [])
    if not key_fields:
        return 'poor'
    
    extracted_count = 0
    for field in key_fields:
        if field in data and data[field] is not None:
            extracted_count += 1
    
    extraction_rate = extracted_count / len(key_fields)
    
    if extraction_rate >= 0.9:
        return 'excellent'
    elif extraction_rate >= 0.7:
        return 'good'
    elif extraction_rate >= 0.5:
        return 'fair'
    else:
        return 'poor'

def generate_report(stats, results, output_file):
    """生成检查报告"""
    report_lines = []
    
    report_lines.append("# PDF信息提取检查报告")
    report_lines.append(f"生成时间: {Path(__file__).parent}")
    report_lines.append(f"分析文件总数: {stats['total_files']}")
    report_lines.append("")
    
    # 1. 总体统计
    report_lines.append("## 1. 总体统计")
    report_lines.append(f"- 处理文件总数: {stats['total_files']}")
    report_lines.append("")
    
    # 2. 文档类型分布
    report_lines.append("## 2. 文档类型分布")
    for doc_type, count in sorted(stats['by_document_type'].items()):
        percentage = (count / stats['total_files']) * 100
        report_lines.append(f"- {doc_type}: {count} 个 ({percentage:.1f}%)")
    report_lines.append(f"- 文档类型识别准确率: {stats['document_type_accuracy_rate']:.1f}%")
    report_lines.append("")
    
    # 3. 提取质量评估
    report_lines.append("## 3. 提取质量评估")
    quality_labels = {
        'excellent': '优秀 (≥90%关键字段)',
        'good': '良好 (70-89%关键字段)',
        'fair': '一般 (50-69%关键字段)',
        'poor': '较差 (<50%关键字段)',
        'error': '提取错误'
    }
    
    for quality, label in quality_labels.items():
        count = stats['extraction_quality'][quality]
        percentage = (count / stats['total_files']) * 100
        report_lines.append(f"- {label}: {count} 个 ({percentage:.1f}%)")
    
    overall_success_rate = ((stats['extraction_quality']['excellent'] + 
                            stats['extraction_quality']['good'] + 
                            stats['extraction_quality']['fair']) / stats['total_files']) * 100
    report_lines.append(f"- **总体功能有效率**: {overall_success_rate:.1f}%")
    report_lines.append("")
    
    # 4. 字段提取率统计
    report_lines.append("## 4. 字段提取率统计")
    report_lines.append("| 字段名 | 提取数量 | 提取率 |")
    report_lines.append("|--------|----------|--------|")
    
    sorted_fields = sorted(stats['field_extraction_rates'].items(), 
                          key=lambda x: x[1]['rate'], reverse=True)
    
    for field, field_stats in sorted_fields:
        if field_stats['total'] > 0:
            rate = field_stats['rate']
            report_lines.append(f"| {field} | {field_stats['extracted']}/{field_stats['total']} | {rate:.1f}% |")
    report_lines.append("")
    
    # 5. 详细结果
    report_lines.append("## 5. 详细提取结果")
    report_lines.append("| 文件名 | 文档类型 | 预期类型 | 提取质量 | 关键字段提取情况 |")
    report_lines.append("|--------|----------|----------|----------|------------------|")
    
    for result in results[:20]:  # 只显示前20个结果
        filename = result['filename'][:30] + "..." if len(result['filename']) > 30 else result['filename']
        doc_type = result['document_type']
        expected_type = result['expected_type'] or "未知"
        quality = result['quality']
        
        # 提取的关键字段
        key_fields = list(result['extracted_fields'].keys())
        key_fields_str = ", ".join(key_fields[:3])
        if len(key_fields) > 3:
            key_fields_str += f" 等{len(key_fields)}个字段"
        
        report_lines.append(f"| {filename} | {doc_type} | {expected_type} | {quality} | {key_fields_str} |")
    
    if len(results) > 20:
        report_lines.append(f"| ... 还有 {len(results) - 20} 个文件 ... | ... | ... | ... | ... |")
    report_lines.append("")
    
    # 6. 问题分析和建议
    report_lines.append("## 6. 问题分析和改进建议")
    
    # 分析常见问题
    poor_results = [r for r in results if r['quality'] in ['poor', 'error']]
    if poor_results:
        report_lines.append("### 发现的问题:")
        for result in poor_results[:5]:  # 只显示前5个问题
            report_lines.append(f"- **{result['filename']}**: {result['document_type']} 类型，质量: {result['quality']}")
            if result['has_error']:
                report_lines.append("  - 提取过程中出现错误")
            else:
                missing_fields = []
                if result['document_type'] == 'VAT_invoice':
                    expected_fields = ['payer', 'seller', 'total_amount', 'date', 'uid']
                    missing_fields = [f for f in expected_fields if f not in result['extracted_fields']]
                elif result['document_type'] == 'bank_receipt':
                    expected_fields = ['payer', 'payee', 'amount', 'date', 'uid']
                    missing_fields = [f for f in expected_fields if f not in result['extracted_fields']]
                elif result['document_type'] == 'tax_certificate':
                    expected_fields = ['payer', 'amount', 'date', 'uid', 'tax_authority']
                    missing_fields = [f for f in expected_fields if f not in result['extracted_fields']]
                
                if missing_fields:
                    report_lines.append(f"  - 缺失字段: {', '.join(missing_fields)}")
    
    # 改进建议
    report_lines.append("### 改进建议:")
    report_lines.append("1. **OCR引擎优化**: 当前使用pdfplumber，建议安装PaddleOCR以提高扫描PDF的识别准确率")
    report_lines.append("2. **字段提取规则优化**: 针对银行水单中的收款方字段，需要优化提取规则")
    report_lines.append("3. **错误处理机制**: 增强错误处理和日志记录")
    report_lines.append("4. **验证机制**: 添加提取结果的验证逻辑，确保数据的合理性")
    report_lines.append("5. **性能优化**: 考虑使用更高效的多线程处理")
    report_lines.append("")
    
    # 7. 总结
    report_lines.append("## 7. 总结")
    report_lines.append(f"PDF信息提取功能总体表现 **{'良好' if overall_success_rate >= 70 else '一般'}**。")
    report_lines.append(f"- 成功处理了 {stats['total_files']} 个PDF文件")
    report_lines.append(f"- 文档类型识别准确率: {stats['document_type_accuracy_rate']:.1f}%")
    report_lines.append(f"- 总体功能有效率: {overall_success_rate:.1f}%")
    report_lines.append(f"- 最佳提取字段: {sorted_fields[0][0]} ({sorted_fields[0][1]['rate']:.1f}%)")
    report_lines.append(f"- 最差提取字段: {sorted_fields[-1][0]} ({sorted_fields[-1][1]['rate']:.1f}%)")
    
    # 写入报告文件
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f"报告已生成: {output_file}")
    
    # 在控制台输出摘要
    print("\n" + "="*60)
    print("PDF提取结果摘要:")
    print("="*60)
    print(f"总文件数: {stats['total_files']}")
    print(f"文档类型识别准确率: {stats['document_type_accuracy_rate']:.1f}%")
    print(f"总体功能有效率: {overall_success_rate:.1f}%")
    print("\n提取质量分布:")
    for quality, label in quality_labels.items():
        count = stats['extraction_quality'][quality]
        percentage = (count / stats['total_files']) * 100
        print(f"  {label}: {count}个 ({percentage:.1f}%)")
    
    return overall_success_rate

def main():
    test_sample_dir = "test sample"
    output_report = "pdf_extraction_analysis_report.md"
    
    print("开始分析PDF提取结果...")
    stats, results = analyze_extraction_results(test_sample_dir)
    
    print(f"\n分析完成，正在生成报告...")
    success_rate = generate_report(stats, results, output_report)
    
    print(f"\n分析完成！总体功能有效率: {success_rate:.1f}%")
    print(f"详细报告已保存到: {output_report}")

if __name__ == "__main__":
    main()