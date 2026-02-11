import logging
from pathlib import Path
import tempfile

try:
    from paddleocr import PaddleOCR
    HAS_PADDLE = True
except ImportError:
    HAS_PADDLE = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    from pdf2image import convert_from_path
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OCREngine:
    def __init__(self, use_gpu=False):
        self.paddle_model = None
        if HAS_PADDLE:
            try:
                # Initialize PaddleOCR - 使用兼容的参数
                # lang='ch' supports both Chinese and English
                self.paddle_model = PaddleOCR(use_angle_cls=True, lang='ch')
                logger.info("PaddleOCR initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize PaddleOCR: {e}")
        else:
            logger.warning("PaddleOCR not installed. OCR capabilities will be limited.")

    def extract_text_from_image(self, image_path):
        """Extract text from image using PaddleOCR"""
        if not self.paddle_model:
            return ""

        try:
            result = self.paddle_model.ocr(str(image_path), cls=True)
            text_parts = []
            for page_result in result:
                if page_result:
                    for item in page_result:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            text_parts.append(item[1][0])  # Extract the recognized text
            return '\n'.join(text_parts)
        except Exception as e:
            logger.error(f"OCR on image failed: {e}")
            return ""

    def convert_pdf_to_images(self, pdf_path):
        """Convert PDF to list of PIL Images"""
        if not HAS_PDF2IMAGE:
            logger.warning("pdf2image not installed. Cannot convert PDF to images for OCR.")
            return []

        try:
            # Convert PDF to images
            pages = convert_from_path(pdf_path)
            return pages
        except Exception as e:
            logger.error(f"Failed to convert PDF to images: {e}")
            return []

    def extract_text(self, pdf_path):
        """
        Extract text from PDF. Tries multiple strategies:
        1. pdfplumber (fast, good for digital PDFs)
        2. If pdfplumber yields little text, use OCR on converted images (good for scanned PDFs)
        """
        text_content = ""

        # Strategy 1: pdfplumber (Digital PDF)
        if HAS_PDFPLUMBER:
            try:
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        # Use layout=True to preserve visual layout (columns, spacing)
                        # This is crucial for invoices with left/right columns
                        extracted = page.extract_text(layout=True)
                        if extracted:
                            text_content += extracted + "\n"

                if len(text_content.strip()) > 50:
                    logger.info("Successfully extracted text using pdfplumber with layout preservation.")
                    return text_content
            except Exception as e:
                logger.error(f"pdfplumber failed: {e}")

        # Strategy 2: PaddleOCR on images (Scanned PDF / Image-based PDF)
        if self.paddle_model and HAS_PDF2IMAGE:
            logger.info("pdfplumber yielded little text. Converting PDF to images and performing OCR...")
            try:
                pages = self.convert_pdf_to_images(pdf_path)
                if pages:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        for i, page in enumerate(pages):
                            temp_img_path = Path(temp_dir) / f"page_{i}.png"
                            page.save(temp_img_path, "PNG")
                            page_text = self.extract_text_from_image(temp_img_path)
                            text_content += page_text + "\n"

                    if text_content.strip():
                        logger.info("Successfully extracted text using OCR on PDF images.")
                        return text_content
            except Exception as e:
                logger.error(f"PDF to image conversion and OCR failed: {e}")
        elif not HAS_PDF2IMAGE:
            logger.warning("pdf2image not available. Cannot process image-based PDFs.")

        return text_content

if __name__ == "__main__":
    engine = OCREngine()
    print(f"PaddleOCR Available: {HAS_PADDLE}")
    print(f"pdfplumber Available: {HAS_PDFPLUMBER}")
