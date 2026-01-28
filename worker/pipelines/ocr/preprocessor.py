"""OCR 전처리 모듈 (Worker용).

파일을 이미지로 변환하는 전처리를 담당합니다.
Backend의 ocr_service.py에서 이식되었습니다.
"""

import os
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from PIL import Image

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.storage import upload_fileobj

# PyMuPDF (fitz) - 크로스 플랫폼 PDF 처리
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF not available. PDF processing will be limited.")

OcrMode = Literal["document", "portray"]


class OcrPreprocessor:
    """OCR 전처리 서비스 (워커용).

    파일을 이미지로 변환합니다.
    """

    def __init__(self):
        self.settings = get_settings()

    def _is_pdf(self, file_path: Path) -> bool:
        """파일이 PDF인지 확인."""
        return file_path.suffix.lower() == ".pdf"

    def _is_image(self, file_path: Path) -> bool:
        """파일이 이미지인지 확인."""
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}
        return file_path.suffix.lower() in image_extensions

    def _is_office_document(self, file_path: Path) -> bool:
        """파일이 Office 문서인지 확인."""
        office_extensions = {".docx", ".doc", ".xls", ".xlsx", ".ppt", ".pptx"}
        return file_path.suffix.lower() in office_extensions

    def _is_txt(self, file_path: Path) -> bool:
        """파일이 텍스트 파일인지 확인."""
        return file_path.suffix.lower() == ".txt"

    def _get_libreoffice_path(self) -> Path:
        """LibreOffice 실행 파일 경로를 반환."""
        # 환경변수 확인
        libreoffice_env = os.getenv("LIBREOFFICE_PATH")
        if libreoffice_env:
            libreoffice_path = Path(libreoffice_env)
            if libreoffice_path.exists():
                return libreoffice_path
            else:
                logger.warning(f"LibreOffice path specified but not found: {libreoffice_path}")

        # 자동 감지
        if sys.platform == "win32":
            common_paths = [
                Path("C:/Program Files/LibreOffice/program/soffice.exe"),
                Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
            ]
        else:
            common_paths = [
                Path("/usr/bin/libreoffice"),
                Path("/usr/local/bin/libreoffice"),
            ]

        for path in common_paths:
            if path.exists():
                logger.info(f"Auto-detected LibreOffice at: {path}")
                return path

        import shutil
        libreoffice_cmd = "soffice.exe" if sys.platform == "win32" else "libreoffice"
        libreoffice_bin = shutil.which(libreoffice_cmd)
        if libreoffice_bin:
            logger.info(f"Found LibreOffice in PATH: {libreoffice_bin}")
            return Path(libreoffice_bin)

        raise FileNotFoundError(
            "LibreOffice not found. Please install LibreOffice and either:\n"
            "1. Add LibreOffice to PATH, or\n"
            "2. Set LIBREOFFICE_PATH environment variable"
        )

    def _office_to_pdf(self, office_path: Path) -> Path:
        """LibreOffice를 사용하여 Office 문서를 PDF로 변환."""
        libreoffice_path = self._get_libreoffice_path()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)

            cmd = [
                str(libreoffice_path),
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(temp_dir_path),
                str(office_path),
            ]

            logger.info(f"Converting Office document to PDF: {office_path}")

            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

            proc = None
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creation_flags if sys.platform == "win32" else 0,
                )

                stdout, stderr = proc.communicate(timeout=300)

                if proc.returncode != 0:
                    error_msg = f"LibreOffice conversion failed (return code: {proc.returncode})"
                    if stderr:
                        error_msg += f"\nError output: {stderr}"
                    raise RuntimeError(error_msg)

            except subprocess.TimeoutExpired:
                if proc:
                    proc.kill()
                    proc.wait()
                raise RuntimeError("LibreOffice conversion timed out after 5 minutes")
            except Exception as e:
                if proc and proc.poll() is None:
                    proc.kill()
                    proc.wait()
                raise RuntimeError(f"Failed to convert Office document to PDF: {e}") from e

            pdf_filename = office_path.stem + ".pdf"
            pdf_path = temp_dir_path / pdf_filename

            if not pdf_path.exists():
                pdf_files = list(temp_dir_path.glob("*.pdf"))
                if pdf_files:
                    pdf_path = pdf_files[0]
                else:
                    raise FileNotFoundError(f"Converted PDF file not found. Expected: {pdf_path}")

            final_pdf_path = Path(tempfile.gettempdir()) / f"ocr_{office_path.stem}_{os.urandom(8).hex()}.pdf"
            import shutil
            shutil.copy2(pdf_path, final_pdf_path)

            logger.info(f"Successfully converted Office document to PDF: {final_pdf_path}")
            return final_pdf_path

    def _pdf_to_images(self, pdf_path: Path, max_dpi: int = 150) -> list[Image.Image]:
        """PDF를 이미지 리스트로 변환."""
        if not PYMUPDF_AVAILABLE:
            raise ImportError("PDF processing requires PyMuPDF. Install with: pip install PyMuPDF")

        try:
            doc = fitz.open(str(pdf_path))
            images: list[Image.Image] = []

            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                mat = fitz.Matrix(max_dpi / 72, max_dpi / 72)
                pix = page.get_pixmap(matrix=mat)

                img_data = pix.tobytes("png")
                img = Image.open(BytesIO(img_data))
                images.append(img)

                pix = None
                img_data = None

            doc.close()
            logger.info(f"Converted PDF to {len(images)} images (DPI: {max_dpi})")
            return images
        except Exception as e:
            logger.error(f"Failed to convert PDF to images: {e}")
            raise

    def _load_image(self, image_path: Path) -> Image.Image:
        """이미지 파일 로드."""
        try:
            return Image.open(image_path)
        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            raise

    def convert_to_images(self, file_path: Path, file_id: int) -> dict[str, Any]:
        """파일을 이미지로 변환합니다.

        Args:
            file_path: 처리할 파일 경로
            file_id: 파일 ID (로깅용)

        Returns:
            {
                "images": list[Image.Image],  # PIL 이미지 리스트
                "page_count": int,            # 페이지 수
                "file_type": str,             # 파일 타입
                "is_text_file": bool,         # 텍스트 파일 여부
                "text_content": str | None,   # 텍스트 파일인 경우 내용
            }
        """
        logger.info(f"[OCR Preprocessor] Converting file to images: {file_path}")

        # 텍스트 파일은 이미지 변환 불필요
        if self._is_txt(file_path):
            logger.info("[OCR Preprocessor] Text file detected, reading directly")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='cp949') as f:
                    text_content = f.read()

            return {
                "images": [],
                "page_count": 1,
                "file_type": ".txt",
                "is_text_file": True,
                "text_content": text_content,
            }

        images: list[Image.Image] = []
        temp_pdf_path: Path | None = None

        try:
            # 파일 타입에 따라 이미지 추출
            if self._is_pdf(file_path):
                images = self._pdf_to_images(file_path)
            elif self._is_image(file_path):
                images = [self._load_image(file_path)]
            elif self._is_office_document(file_path):
                temp_pdf_path = self._office_to_pdf(file_path)
                images = self._pdf_to_images(temp_pdf_path)
            else:
                raise ValueError(f"Unsupported file type: {file_path.suffix}")

            logger.info(
                "[OCR Preprocessor] Conversion completed: file_id=%s, count=%d",
                file_id, len(images)
            )

            return {
                "images": images,
                "page_count": len(images),
                "file_type": file_path.suffix.lower(),
                "is_text_file": False,
                "text_content": None,
            }

        finally:
            # 임시 PDF 파일 삭제
            if temp_pdf_path and temp_pdf_path.exists():
                try:
                    temp_pdf_path.unlink()
                    logger.debug(f"[OCR Preprocessor] Deleted temp PDF: {temp_pdf_path}")
                except Exception as e:
                    logger.warning(f"[OCR Preprocessor] Failed to delete temp PDF: {e}")
