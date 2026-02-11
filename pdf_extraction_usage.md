# PDF 信息提取独立运行说明

本文档说明如何在本项目中**单独运行 PDF 信息提取程序**，包括环境准备、命令行使用方式以及在代码中调用的示例。

统一入口脚本为：

- 源码路径：`src/pdf_extraction_service.py`

该入口已经内置：

- PaddleOCR 模型单例 + 按需加载
- 内存管理（图片资源释放、垃圾回收）
- 日志输出（处理开始/完成和错误信息）

---

## 一、环境准备（首次使用需要）

项目根目录：

```text
c:\Users\wenwe\WPSDrive\1509592323\WPS企业云盘\ISCHN\团队文档\财务部\1.0 核算管理\1.2 美国公司\Expensify Auto
```

1. 打开 PowerShell 或命令提示符。

2. 切换到项目根目录：

   ```powershell
   cd "c:\Users\wenwe\WPSDrive\1509592323\WPS企业云盘\ISCHN\团队文档\财务部\1.0 核算管理\1.2 美国公司\Expensify Auto"
   ```

3. 运行环境安装脚本（创建 `.venv` 虚拟环境并安装依赖）：

   ```powershell
   .\setup.bat
   ```

4. 激活虚拟环境（之后建议所有运行都在虚拟环境内进行）：

   ```powershell
   .\.venv\Scripts\activate
   ```

   看到命令行前面多了 `(.venv)` 前缀，说明激活成功。

---

## 二、统一 PDF 提取服务入口

核心入口脚本：

- `src/pdf_extraction_service.py`

它负责：

- 统一管理 OCR 模型的创建和复用（单例 + 按需加载）
- 控制内存使用（图像对象关闭、垃圾回收）
- 标准化日志输出格式
- 将提取结果写入 JSON 文件

### 命令行参数说明

`pdf_extraction_service.py` 支持两类输入：

- **单个 PDF 文件路径**
- **目录路径**（批量处理目录中所有 `.pdf` 文件）

可用参数：

- `input_path`（必选）  
  PDF 文件路径或目录路径。

- `--output-dir`（可选）  
  JSON 输出目录，默认是项目根目录下的 `temp` 目录。

- `--workers`（可选）  
  多线程工作线程数（默认 4）。仅在批量处理且未指定 `--sequential` 时生效。

- `--sequential`（可选，无值开关）  
  强制顺序处理所有 PDF 文件，适合内存敏感场景。

---

## 三、处理单个 PDF 文件

示例：只处理一张测试发票：

```powershell
cd "c:\Users\wenwe\WPSDrive\1509592323\WPS企业云盘\ISCHN\团队文档\财务部\1.0 核算管理\1.2 美国公司\Expensify Auto"
.\.venv\Scripts\activate

python src\pdf_extraction_service.py `
  "test sample\20260107 INVINNR 26127000000026876932 滴滴出行科技 交通费 3,280.29.pdf" `
  --sequential
```

说明：

- 输出 JSON 默认写入：`项目根目录\temp\`
- 输出文件名格式：`原PDF文件名_extracted_revised.json`  
  如：`20260107 INVINNR 26127000000026876932 滴滴出行科技 交通费 3,280.29_extracted_revised.json`

控制台日志示例：

```text
2026-02-11 14:02:03,666 INFO __main__ 开始处理: 20260107 INVINNR ... 交通费 3,280.29.pdf
2026-02-11 14:02:03,730 INFO ocr_engine Successfully extracted text using pdfplumber with layout preservation.
2026-02-11 14:02:03,745 INFO __main__ 完成处理: 20260107 INVINNR ... 交通费 3,280.29.pdf
```

---

## 四、批量处理整个目录

假设要处理 `test sample` 目录下的所有 PDF 文件。

### 1. 顺序处理（推荐，内存更稳）

```powershell
python src\pdf_extraction_service.py "test sample" --sequential
```

特性：

- 单线程顺序处理所有 PDF
- 内存占用更平滑，适合大批量文件且机器内存相对有限的情况

### 2. 多线程处理（速度更快）

例如使用 4 个线程：

```powershell
python src\pdf_extraction_service.py "test sample" --workers 4
```

说明：

- 不加 `--sequential`，则根据 `--workers` 启用多线程
- 吞吐更高，但每个线程内仍会复用同一个 OCR 引擎实例，因此总体内存控制优于“每个任务都重新加载模型”的方式

### 3. 自定义输出目录

如将 JSON 输出到 `D:\pdf_outputs`：

```powershell
python src\pdf_extraction_service.py "test sample" `
  --output-dir "D:\pdf_outputs" `
  --sequential
```

---

## 五、日志与错误观察

### 日志级别与内容

`pdf_extraction_service.py` 默认配置了日志输出格式：

- 时间戳
- 日志级别（INFO / ERROR 等）
- 模块名
- 具体消息

常见日志信息：

- `开始处理: xxx.pdf`
- `Successfully extracted text using pdfplumber...`
- `PDF to image conversion and OCR failed: ...`
- `完成处理: xxx.pdf`

### PaddleOCR 初始化失败的情况

当 PaddleOCR 初始化失败（例如内存不足或底层库报错）时：

- 会记录错误日志 `Failed to initialize PaddleOCR: ...`
- 内部会标记 `_model_init_failed`，后续不会反复尝试重新初始化模型，避免不断拖垮内存
- 在这种情况下，程序依然会尽量使用 `pdfplumber` 处理可解析的 PDF

---

## 六、在自己的 Python 代码中调用（可选）

如果需要在其它 Python 脚本中直接复用提取能力，可以导入服务类：

```python
from pathlib import Path
from src.pdf_extraction_service import PDFExtractionService

service = PDFExtractionService()

# 处理单个 PDF
pdf_path = Path("test sample") / "20260107 INVINNR 26127000000026876932 滴滴出行科技 交通费 3,280.29.pdf"
filename, result = service.process_pdf(pdf_path)

# 批量顺序处理
pdf_dir = Path("test sample")
pdf_paths = sorted(pdf_dir.glob("*.pdf"))
results = service.process_pdfs_sequentially(pdf_paths)

service.close()
```

说明：

- `process_pdf` 会：
  - 解析指定 PDF
  - 写出 JSON 文件到输出目录
  - 返回 `(文件名, 结果字典)`
- `service.close()` 会释放 OCR 模型并触发一次垃圾回收，有利于长期运行的进程控制内存。

---

## 七、使用建议总结

- **小批量 / 内存优先**：使用 `--sequential` 顺序模式。
- **大批量 / 性能优先**：适当增大 `--workers`，例如 `--workers 4`，但建议逐步压测。
- **输出管理**：定期清理 `temp` 目录中的历史 JSON，避免磁盘堆积。
- **异常场景**：若看到与 OpenBLAS 或 PaddleOCR 相关的内存错误，建议：
  - 优先使用 `--sequential` 模式
  - 或分批次处理 PDF 文件（例如每次 10–15 个）。

