"""OCR 서비스 - Qwen3-VL 모델을 사용한 문서 OCR 처리."""

import base64
import json
import time
from pathlib import Path
from typing import Any
from io import BytesIO

from PIL import Image
from ..core.config import get_settings
from ..core.logging import logger

# PyMuPDF (fitz) - 크로스 플랫폼 PDF 처리 (poppler 대안)
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF not available. PDF processing will be limited.")

# pdf2image (poppler 기반) - 하위 호환성을 위해 유지
try:
    from pdf2image import convert_from_path
    from pdf2image.exceptions import PDFInfoNotInstalledError
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False


class OcrService:
    """OCR 처리 서비스."""

    def __init__(self):
        self.settings = get_settings()

    def _is_pdf(self, file_path: Path) -> bool:
        """파일이 PDF인지 확인."""
        return file_path.suffix.lower() == ".pdf"

    def _is_image(self, file_path: Path) -> bool:
        """파일이 이미지인지 확인."""
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}
        return file_path.suffix.lower() in image_extensions

    def _pdf_to_images(self, pdf_path: Path) -> list[Image.Image]:
        """PDF를 이미지 리스트로 변환."""
        # 우선순위: PyMuPDF (크로스 플랫폼, 의존성 없음) > pdf2image (poppler 필요)
        if PYMUPDF_AVAILABLE:
            return self._pdf_to_images_pymupdf(pdf_path)
        elif PDF2IMAGE_AVAILABLE:
            return self._pdf_to_images_pdf2image(pdf_path)
        else:
            raise ImportError(
                "PDF processing requires either PyMuPDF or pdf2image. "
                "Install with: pip install PyMuPDF (recommended) or pip install pdf2image"
            )
    
    def _pdf_to_images_pymupdf(self, pdf_path: Path) -> list[Image.Image]:
        """PyMuPDF를 사용하여 PDF를 이미지로 변환 (크로스 플랫폼, 의존성 없음)."""
        try:
            doc = fitz.open(str(pdf_path))
            images: list[Image.Image] = []
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                # 300 DPI로 렌더링 (OCR에 최적화)
                mat = fitz.Matrix(300 / 72, 300 / 72)  # 72 DPI를 300 DPI로 변환
                pix = page.get_pixmap(matrix=mat)
                
                # PIL Image로 변환
                img_data = pix.tobytes("png")
                img = Image.open(BytesIO(img_data))
                images.append(img)
            
            doc.close()
            logger.info(f"Converted PDF to {len(images)} images using PyMuPDF")
            return images
        except Exception as e:
            logger.error(f"Failed to convert PDF to images using PyMuPDF: {e}")
            raise
    
    def _pdf_to_images_pdf2image(self, pdf_path: Path) -> list[Image.Image]:
        """pdf2image를 사용하여 PDF를 이미지로 변환 (poppler 필요, 하위 호환성)."""
        try:
            # poppler 경로가 설정되어 있으면 사용
            poppler_path = self.settings.poppler_path if self.settings.poppler_path else None
            if poppler_path:
                logger.info(f"Using poppler from: {poppler_path}")
                images = convert_from_path(str(pdf_path), dpi=300, poppler_path=poppler_path)
            else:
                images = convert_from_path(str(pdf_path), dpi=300)
            logger.info(f"Converted PDF to {len(images)} images using pdf2image")
            return images
        except PDFInfoNotInstalledError as e:
            error_msg = (
                "Poppler is not installed or not in PATH. "
                "Please install poppler for Windows and either:\n"
                "1. Add poppler bin directory to PATH, or\n"
                "2. Set POPPLER_PATH environment variable (e.g., POPPLER_PATH=C:\\poppler\\bin)\n"
                "3. Or install PyMuPDF instead: pip install PyMuPDF (recommended, no external dependencies)\n"
                f"Download poppler from: https://github.com/oschwartz10612/poppler-windows/releases"
            )
            logger.error(f"Failed to convert PDF to images: {error_msg}")
            raise RuntimeError(error_msg) from e
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

    def _image_to_base64(self, image: Image.Image) -> str:
        """이미지를 base64 문자열로 변환."""
        buffered = BytesIO()
        # RGB 모드로 변환 (PNG의 경우 RGBA일 수 있음)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(buffered, format="JPEG", quality=95)
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return img_str

    def _call_llm_api(self, prompt: str, image_base64: str | None = None, server_process=None) -> str:
        """
        LLM API를 호출하여 OCR 수행.
        
        Args:
            prompt: OCR 프롬프트
            image_base64: base64 인코딩된 이미지 (선택)
            server_process: llama-server 프로세스 (이미 시작된 경우, None이면 자동 시작)
        """
        import httpx
        from contextlib import nullcontext
        from ..worker.llamacpp_server_client import _llama_server_process
        
        # llama-server가 이미 시작된 경우 (process_document에서 관리)
        # 그렇지 않으면 자동으로 시작
        if server_process is None:
            server_context = _llama_server_process(self.settings)
        else:
            # 이미 시작된 서버 사용 (컨텍스트 매니저 없이)
            server_context = nullcontext(server_process)
        
        with server_context:
            # llama.cpp 서버 API 사용
            url = f"{self.settings.llm_api_base_url}/v1/chat/completions"
            
            messages = []
            if image_base64:
                # Vision 모델을 사용하는 경우 이미지를 포함
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                })
            else:
                messages.append({
                    "role": "user",
                    "content": prompt
                })
            
            payload = {
                "model": self.settings.llm_api_model_name,
                "messages": messages,
                "temperature": 0.1,  # OCR은 낮은 temperature 사용
                "max_tokens": 4096,  # 긴 문서를 위해 충분한 토큰
            }
            
            # 503 에러 발생 시 재시도 로직
            max_retries = 3
            retry_delay = 3  # 3초 대기
            
            for attempt in range(max_retries):
                try:
                    with httpx.Client(timeout=300.0) as client:  # 5분 타임아웃
                        response = client.post(url, json=payload)
                        
                        # 503 에러인 경우 재시도
                        if response.status_code == 503:
                            if attempt < max_retries - 1:
                                logger.warning(
                                    f"LLM API returned 503 (Service Unavailable), retrying in {retry_delay}s "
                                    f"(attempt {attempt + 1}/{max_retries})"
                                )
                                time.sleep(retry_delay)
                                continue
                            else:
                                # 마지막 시도에서도 503이면 에러 발생
                                response.raise_for_status()
                        
                        response.raise_for_status()
                        result = response.json()
                        
                        # 응답에서 텍스트 추출
                        if "choices" in result and len(result["choices"]) > 0:
                            content = result["choices"][0].get("message", {}).get("content", "")
                            return content.strip()
                        else:
                            raise ValueError(f"Unexpected API response: {result}")
                except httpx.HTTPStatusError as e:
                    # 503이 아닌 다른 HTTP 에러는 즉시 실패
                    if e.response.status_code != 503:
                        logger.error(f"HTTP error calling LLM API: {e}")
                        raise
                    # 503 에러는 재시도 루프에서 처리됨
                    if attempt == max_retries - 1:
                        logger.error(f"LLM API returned 503 after {max_retries} attempts: {e}")
                        raise
                except httpx.HTTPError as e:
                    logger.error(f"HTTP error calling LLM API: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Error calling LLM API: {e}")
                    raise

    def process_document(self, file_path: Path) -> dict[str, Any]:
        """
        문서 파일을 OCR 처리.
        
        Returns:
            {
                "ocr_text": str,  # 추출된 텍스트
                "page_count": int,  # 페이지 수
                "ocr_metadata": dict  # 메타데이터
            }
        """
        from ..worker.llamacpp_server_client import _llama_server_process
        
        logger.info(f"Processing document: {file_path}")
        
        images: list[Image.Image] = []
        page_count = 0
        
        # 파일 타입에 따라 이미지 로드
        if self._is_pdf(file_path):
            if not PYMUPDF_AVAILABLE and not PDF2IMAGE_AVAILABLE:
                raise ImportError(
                    "PDF processing requires either PyMuPDF or pdf2image. "
                    "Install with: pip install PyMuPDF (recommended) or pip install pdf2image"
                )
            images = self._pdf_to_images(file_path)
            page_count = len(images)
        elif self._is_image(file_path):
            images = [self._load_image(file_path)]
            page_count = 1
        else:
            raise ValueError(f"Unsupported file type: {file_path.suffix}")
        
        logger.info(f"Processing {page_count} page(s)")
        
        # llama-server를 한 번만 시작하고 모든 페이지 처리 후 종료
        # (각 페이지마다 시작/종료하는 것보다 훨씬 효율적)
        with _llama_server_process(self.settings) as server_process:
            # 각 페이지를 OCR 처리
            all_texts: list[str] = []
            ocr_metadata: dict[str, Any] = {
                "file_path": str(file_path),
                "file_type": file_path.suffix.lower(),
                "page_count": page_count,
                "pages": []
            }
            
            for page_idx, image in enumerate(images):
                logger.info(f"Processing page {page_idx + 1}/{page_count}")
                
                # 이미지를 base64로 변환
                image_base64 = self._image_to_base64(image)
                
                # OCR 프롬프트
                prompt = """이 이미지에서 모든 텍스트를 정확하게 추출해주세요. 
이미지에 있는 모든 텍스트, 숫자, 기호를 그대로 추출하되, 
원본의 형식과 구조를 최대한 유지해주세요.
표나 목록이 있다면 그 구조도 유지해주세요.
추출된 텍스트만 반환하고, 설명이나 주석은 포함하지 마세요."""
                
                try:
                    # LLM API 호출 (이미 시작된 서버 사용)
                    page_text = self._call_llm_api(prompt, image_base64, server_process=server_process)
                    all_texts.append(page_text)
                    
                    ocr_metadata["pages"].append({
                        "page_number": page_idx + 1,
                        "text_length": len(page_text),
                        "status": "success"
                    })
                    
                    logger.info(f"Page {page_idx + 1} OCR completed ({len(page_text)} chars)")
                except Exception as e:
                    logger.error(f"Failed to process page {page_idx + 1}: {e}")
                    ocr_metadata["pages"].append({
                        "page_number": page_idx + 1,
                        "status": "failed",
                        "error": str(e)
                    })
                    # 실패한 페이지는 빈 텍스트로 처리하되, 전체 OCR 실패 시 예외 발생
                    all_texts.append("")
                    # 첫 페이지 실패 시 즉시 예외 발생 (LLM API 연결 문제 등)
                    if page_idx == 0:
                        raise RuntimeError(
                            f"OCR failed for first page (likely LLM API connection issue): {e}. "
                            f"Please ensure LLM API server is running at {self.settings.llm_api_base_url}"
                        ) from e
        
        # 모든 페이지 텍스트 결합
        ocr_text = "\n\n--- Page Break ---\n\n".join(all_texts)
        
        # 모든 페이지가 실패한 경우 예외 발생
        if not ocr_text.strip():
            failed_pages = [p for p in ocr_metadata["pages"] if p.get("status") == "failed"]
            if failed_pages:
                errors = [p.get("error", "Unknown error") for p in failed_pages]
                error_summary = "; ".join(set(errors[:3]))  # 최대 3개 고유 에러만 표시
                raise RuntimeError(
                    f"All {page_count} page(s) OCR processing failed. "
                    f"Errors: {error_summary}. "
                    f"Please ensure LLM API server is running at {self.settings.llm_api_base_url}"
                )
        
        logger.info(f"OCR completed: {len(ocr_text)} total characters")
        
        return {
            "ocr_text": ocr_text,
            "page_count": page_count,
            "ocr_metadata": ocr_metadata
        }

