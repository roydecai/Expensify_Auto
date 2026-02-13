import logging
import os
from pathlib import Path
import tempfile
import threading
import gc

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

logger = logging.getLogger(__name__)

class OCREngine:
    _instances = {}
    _instance_lock = threading.Lock()

    def __new__(cls, use_gpu=False):
        key = (use_gpu,)
        with cls._instance_lock:
            instance = cls._instances.get(key)
            if instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[key] = instance
        return instance

    def __init__(self, use_gpu=False):
        if self._initialized:
            return
        self.use_gpu = use_gpu
        self.paddle_model = None
        self._model_lock = threading.Lock()
        self._ocr_lock = threading.Lock()
        self._model_init_failed = False
        self._initialized = True
        if not HAS_PADDLE:
            logger.warning("PaddleOCR not installed. OCR capabilities will be limited.")

    def _ensure_paddle_model(self):
        if not HAS_PADDLE:
            return False
        if self._model_init_failed:
            return False
        if self.paddle_model:
            return True
        with self._model_lock:
            if self.paddle_model:
                return True
            try:
                os.environ.setdefault("OMP_NUM_THREADS", "1")
                os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
                os.environ.setdefault("MKL_NUM_THREADS", "1")
                os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
                self.paddle_model = PaddleOCR(use_angle_cls=True, lang='ch')
                logger.info("PaddleOCR initialized successfully.")
                return True
            except Exception as e:
                logger.error(f"Failed to initialize PaddleOCR: {e}")
                self.paddle_model = None
                self._model_init_failed = True
                return False

    def release(self):
        self.paddle_model = None
        self._model_init_failed = False
        gc.collect()

    def extract_text_from_image(self, image_path):
        """Extract text from image using PaddleOCR"""
        if not self._ensure_paddle_model():
            return ""

        try:
            with self._ocr_lock:
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
        if HAS_PDF2IMAGE and self._ensure_paddle_model():
            logger.info("pdfplumber yielded little text. Converting PDF to images and performing OCR...")
            pages = []
            try:
                pages = self.convert_pdf_to_images(pdf_path)
                if pages:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        for i, page in enumerate(pages):
                            temp_img_path = Path(temp_dir) / f"page_{i}.png"
                            page.save(temp_img_path, "PNG")
                            page_text = self.extract_text_from_image(temp_img_path)
                            text_content += page_text + "\n"
                            close_fn = getattr(page, "close", None)
                            if callable(close_fn):
                                close_fn()

                    if text_content.strip():
                        logger.info("Successfully extracted text using OCR on PDF images.")
                        return text_content
            except Exception as e:
                logger.error(f"PDF to image conversion and OCR failed: {e}")
            finally:
                for page in pages:
                    close_fn = getattr(page, "close", None)
                    if callable(close_fn):
                        close_fn()
                gc.collect()
        elif not HAS_PDF2IMAGE:
            logger.warning("pdf2image not available. Cannot process image-based PDFs.")

        return text_content

if __name__ == "__main__":
    engine = OCREngine()
    print(f"PaddleOCR Available: {HAS_PADDLE}")
    print(f"pdfplumber Available: {HAS_PDFPLUMBER}")
