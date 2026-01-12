"""OCR 전처리 서비스.

파일을 이미지로 변환하는 전처리만 담당합니다.
실제 OCR (LLM Vision API 호출)은 워커에서 수행합니다.
"""

import base64
import os
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from PIL import Image

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import upload_fileobj

# PyMuPDF (fitz) - 크로스 플랫폼 PDF 처리
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF not available. PDF processing will be limited.")

OcrMode = Literal["document", "portray"]


class OcrPreprocessor:
    """OCR 전처리 서비스 (백엔드용).
    
    파일을 이미지로 변환하고 S3에 임시 저장합니다.
    실제 OCR은 워커에서 수행합니다.
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
        if self.settings.libreoffice_path:
            libreoffice_path = Path(self.settings.libreoffice_path)
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

    def _image_to_base64(self, image: Image.Image, max_size: tuple[int, int] = (1536, 1536), quality: int = 75) -> str:
        """이미지를 base64 문자열로 변환."""
        import gc
        
        buffered = BytesIO()
        try:
            if image.mode != "RGB":
                image = image.convert("RGB")
            
            original_size = image.size
            if original_size[0] > max_size[0] or original_size[1] > max_size[1]:
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                logger.info(f"Image resized from {original_size} to {image.size}")
            
            image.save(buffered, format="JPEG", quality=quality, optimize=True)
            img_str = base64.b64encode(buffered.getvalue()).decode()
            
            return img_str
        finally:
            buffered.close()
            gc.collect()

    def _save_image_to_s3(self, image: Image.Image, s3_key: str) -> str:
        """이미지를 S3에 저장하고 key를 반환."""
        import gc
        
        buffered = BytesIO()
        try:
            if image.mode != "RGB":
                image = image.convert("RGB")
            
            # OCR 처리를 위한 적절한 크기로 리사이즈
            max_size = (2048, 2048)
            original_size = image.size
            if original_size[0] > max_size[0] or original_size[1] > max_size[1]:
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                logger.debug(f"Image resized from {original_size} to {image.size}")
            
            image.save(buffered, format="JPEG", quality=85, optimize=True)
            buffered.seek(0)
            
            # S3에 업로드
            upload_fileobj(buffered, key=s3_key)
            logger.debug(f"Image uploaded to S3: {s3_key}")
            
            return s3_key
        finally:
            buffered.close()
            gc.collect()

    def prepare_for_ocr(self, file_path: Path, file_id: int) -> dict[str, Any]:
        """파일을 OCR 처리를 위해 준비합니다.
        
        파일을 이미지로 변환하고 S3에 임시 저장합니다.
        
        Args:
            file_path: 처리할 파일 경로
            file_id: 파일 ID (S3 경로 생성용)
            
        Returns:
            {
                "image_s3_keys": list[str],  # 이미지 S3 경로 목록
                "page_count": int,           # 페이지 수
                "file_type": str,            # 파일 타입
                "is_text_file": bool,        # 텍스트 파일 여부
                "text_content": str | None,  # 텍스트 파일인 경우 내용
            }
        """
        logger.info(f"Preparing file for OCR: {file_path}")
        
        # 텍스트 파일은 OCR 불필요
        if self._is_txt(file_path):
            logger.info("Text file detected, reading directly")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='cp949') as f:
                    text_content = f.read()
            
            return {
                "image_s3_keys": [],
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
            
            # 이미지들을 S3에 임시 저장
            image_s3_keys: list[str] = []
            batch_id = uuid4().hex[:8]
            
            for idx, image in enumerate(images):
                s3_key = f"temp/ocr/{file_id}/{batch_id}/page_{idx + 1}.jpg"
                logger.debug(
                    "[OCR Preprocessor] 이미지 업로드 시작: file_id=%s, page=%d/%d, key=%s",
                    file_id, idx + 1, len(images), s3_key
                )
                self._save_image_to_s3(image, s3_key)
                image_s3_keys.append(s3_key)
                logger.info(
                    "[OCR Preprocessor] 이미지 업로드 완료: file_id=%s, page=%d/%d, key=%s",
                    file_id, idx + 1, len(images), s3_key
                )
                
                # 메모리 정리
                try:
                    image.close()
                except Exception:
                    pass
            
            logger.info(
                "[OCR Preprocessor] 모든 이미지 준비 완료: file_id=%s, count=%d, keys=%s",
                file_id, len(image_s3_keys), image_s3_keys
            )
            
            return {
                "image_s3_keys": image_s3_keys,
                "page_count": len(image_s3_keys),
                "file_type": file_path.suffix.lower(),
                "is_text_file": False,
                "text_content": None,
            }
            
        finally:
            # 임시 PDF 파일 삭제
            if temp_pdf_path and temp_pdf_path.exists():
                try:
                    temp_pdf_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete temp PDF: {e}")
            
            # 이미지 메모리 정리
            for img in images:
                try:
                    img.close()
                except Exception:
                    pass


# 하위 호환성을 위해 OcrService 별칭 유지
OcrService = OcrPreprocessor
