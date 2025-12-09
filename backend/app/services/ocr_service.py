"""OCR 서비스 - Qwen3-VL 모델을 사용한 문서 OCR 처리."""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
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

# Docling - 문서 구조 파싱 및 HTML 추출
try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    logger.warning("Docling not available. Complex document layout processing will be limited.")


OcrMode = Literal["basic", "docling"]


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

    def _is_office_document(self, file_path: Path) -> bool:
        """파일이 Office 문서인지 확인 (DOCX, DOC, XLS, XLSX, PPT, PPTX)."""
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
        
        # 자동 감지: Windows와 Linux에서 일반적인 경로 확인
        if sys.platform == "win32":
            # Windows: 일반적인 설치 경로들
            common_paths = [
                Path("C:/Program Files/LibreOffice/program/soffice.exe"),
                Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
            ]
        else:
            # Linux: PATH에서 찾기
            common_paths = [
                Path("/usr/bin/libreoffice"),
                Path("/usr/local/bin/libreoffice"),
            ]
        
        for path in common_paths:
            if path.exists():
                logger.info(f"Auto-detected LibreOffice at: {path}")
                return path
        
        # PATH에서 찾기 시도
        import shutil
        libreoffice_cmd = "soffice.exe" if sys.platform == "win32" else "libreoffice"
        libreoffice_bin = shutil.which(libreoffice_cmd)
        if libreoffice_bin:
            logger.info(f"Found LibreOffice in PATH: {libreoffice_bin}")
            return Path(libreoffice_bin)
        
        raise FileNotFoundError(
            "LibreOffice not found. Please install LibreOffice and either:\n"
            "1. Add LibreOffice to PATH, or\n"
            "2. Set LIBREOFFICE_PATH environment variable\n"
            f"Windows example: LIBREOFFICE_PATH=C:\\Program Files\\LibreOffice\\program\\soffice.exe\n"
            f"Linux example: LIBREOFFICE_PATH=/usr/bin/libreoffice"
        )

    def _office_to_pdf(self, office_path: Path) -> Path:
        """
        LibreOffice를 사용하여 Office 문서를 PDF로 변환.
        
        Args:
            office_path: Office 문서 파일 경로
            
        Returns:
            변환된 PDF 파일 경로
        """
        import tempfile
        
        libreoffice_path = self._get_libreoffice_path()
        
        # 임시 디렉토리 생성 (변환된 PDF 저장용)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            
            # LibreOffice 명령어 구성
            # --headless: GUI 없이 실행
            # --convert-to pdf: PDF로 변환
            # --outdir: 출력 디렉토리
            cmd = [
                str(libreoffice_path),
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(temp_dir_path),
                str(office_path),
            ]
            
            logger.info(f"Converting Office document to PDF: {office_path}")
            logger.debug(f"LibreOffice command: {' '.join(cmd)}")
            
            # Windows에서 프로세스 그룹 생성 플래그 설정
            creation_flags = 0
            if sys.platform == "win32":
                # CREATE_NEW_PROCESS_GROUP: 프로세스 그룹 생성 (종료 시 자식 프로세스까지 종료 가능)
                # CREATE_NO_WINDOW: 콘솔 창 숨기기
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            
            # LibreOffice 실행
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
                
                # 프로세스 완료 대기
                stdout, stderr = proc.communicate(timeout=300)  # 최대 5분 대기
                
                # 출력 로그
                if stdout:
                    for line in stdout.strip().split('\n'):
                        if line.strip():
                            logger.debug(f"[LibreOffice] {line}")
                
                if stderr:
                    for line in stderr.strip().split('\n'):
                        if line.strip():
                            # 경고 메시지는 debug 레벨로
                            if 'warning' in line.lower() or 'info' in line.lower():
                                logger.debug(f"[LibreOffice] {line}")
                            else:
                                logger.warning(f"[LibreOffice] {line}")
                
                if proc.returncode != 0:
                    error_msg = f"LibreOffice conversion failed (return code: {proc.returncode})"
                    if stderr:
                        error_msg += f"\nError output: {stderr}"
                    raise RuntimeError(error_msg)
                
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError("LibreOffice conversion timed out after 5 minutes")
            except Exception as e:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                raise RuntimeError(f"Failed to convert Office document to PDF: {e}") from e
            
            # 변환된 PDF 파일 찾기
            # LibreOffice는 원본 파일명을 기반으로 PDF 파일명을 생성합니다
            # 예: document.docx -> document.pdf
            pdf_filename = office_path.stem + ".pdf"
            pdf_path = temp_dir_path / pdf_filename
            
            if not pdf_path.exists():
                # 대소문자 구분 없는 파일 시스템을 고려하여 다시 찾기
                pdf_files = list(temp_dir_path.glob("*.pdf"))
                if pdf_files:
                    pdf_path = pdf_files[0]
                    logger.info(f"Found PDF file with different case: {pdf_path.name}")
                else:
                    raise FileNotFoundError(
                        f"Converted PDF file not found. Expected: {pdf_path}\n"
                        f"Available files in temp directory: {list(temp_dir_path.iterdir())}"
                    )
            
            # 변환된 PDF를 영구 임시 파일로 복사 (호출자가 삭제해야 함)
            # process_document에서 사용 후 삭제
            final_pdf_path = Path(tempfile.gettempdir()) / f"ocr_{office_path.stem}_{os.urandom(8).hex()}.pdf"
            import shutil
            shutil.copy2(pdf_path, final_pdf_path)
            
            logger.info(f"Successfully converted Office document to PDF: {final_pdf_path}")
            return final_pdf_path

    def _pdf_to_images(self, pdf_path: Path) -> list[Image.Image]:
        """PDF를 이미지 리스트로 변환."""
        if not PYMUPDF_AVAILABLE:
            raise ImportError(
                "PDF processing requires PyMuPDF. "
                "Install with: pip install PyMuPDF"
            )
        return self._pdf_to_images_pymupdf(pdf_path)
    
    def _pdf_to_images_pymupdf(self, pdf_path: Path, max_dpi: int = 150) -> list[Image.Image]:
        """
        PyMuPDF를 사용하여 PDF를 이미지로 변환 (크로스 플랫폼, 의존성 없음).
        
        Args:
            pdf_path: PDF 파일 경로
            max_dpi: 최대 DPI (기본값 150, 성능 우선)
                     150: 성능 우선 (품질 약간 저하 가능) ⭐ 기본값
                     200: 권장 (품질과 성능의 좋은 균형)
                     250-300: 고품질 (메모리/처리 부담 증가)
        """
        try:
            doc = fitz.open(str(pdf_path))
            images: list[Image.Image] = []
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                # DPI 제한 (메모리 및 처리 시간 최적화)
                # 200 DPI는 OCR 품질과 성능의 좋은 균형점
                # 300 DPI는 매우 큰 이미지를 생성하므로 메모리 부담 증가
                mat = fitz.Matrix(max_dpi / 72, max_dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                
                # PIL Image로 변환
                img_data = pix.tobytes("png")
                img = Image.open(BytesIO(img_data))
                images.append(img)
                
                # 메모리 정리
                pix = None
                img_data = None
            
            doc.close()
            logger.info(f"Converted PDF to {len(images)} images using PyMuPDF (DPI: {max_dpi})")
            return images
        except Exception as e:
            logger.error(f"Failed to convert PDF to images using PyMuPDF: {e}")
            raise
    
    def _load_image(self, image_path: Path) -> Image.Image:
        """이미지 파일 로드."""
        try:
            return Image.open(image_path)
        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            raise

    def _image_to_base64(self, image: Image.Image, max_size: tuple[int, int] = (1536, 1536), quality: int = 75) -> str:
        """
        이미지를 base64 문자열로 변환.
        
        Args:
            image: PIL Image 객체
            max_size: 최대 이미지 크기 (width, height). 초과 시 리사이즈
                     기본값 1536x1536으로 설정 (메모리 및 처리 부하 감소)
            quality: JPEG 압축 품질 (1-100, 낮을수록 파일 크기 작음)
                     기본값 75로 설정 (메모리 사용량 감소)
        """
        import gc
        
        buffered = BytesIO()
        try:
            # RGB 모드로 변환 (PNG의 경우 RGBA일 수 있음)
            # 원본 이미지를 수정하지 않기 위해 복사본 생성
            if image.mode != "RGB":
                image = image.convert("RGB")
            
            # 이미지 크기 제한 (메모리 및 처리 시간 최적화)
            # 1536x1536으로 제한하여 Vision Encoder 처리 부하 감소
            original_size = image.size
            if original_size[0] > max_size[0] or original_size[1] > max_size[1]:
                # 비율 유지하며 리사이즈
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                logger.info(
                    f"Image resized from {original_size} to {image.size} "
                    f"(max_size={max_size})"
                )
            
            # JPEG로 저장 (품질 낮춰서 파일 크기 및 메모리 사용량 감소)
            # quality=75로 설정하여 메모리 사용량 감소
            image.save(buffered, format="JPEG", quality=quality, optimize=True)
            img_str = base64.b64encode(buffered.getvalue()).decode()
            
            return img_str
        finally:
            # 메모리 정리
            buffered.close()
            # 명시적 가비지 컬렉션으로 메모리 해제 촉진
            gc.collect()

    def _get_default_ocr_prompt(self) -> str:
        """기본 OCR 프롬프트 (표가 없는 경우)."""
        return """이 이미지에서 모든 텍스트를 정확하게 추출하여 **반드시 HTML 형식으로만** 반환해주세요.

**중요: 마크다운 문법(#, ##, |, -, 등)을 절대 사용하지 마세요. 오직 HTML 태그만 사용하세요.**

요구사항:
1. 제목은 반드시 <h1>, <h2>, <h3> 등의 HTML 헤더 태그를 사용하세요. # 또는 ## 같은 마크다운 문법은 사용하지 마세요.
2. 목록은 반드시 <ul>, <ol>, <li> 태그를 사용하세요. - 또는 1. 같은 마크다운 문법은 사용하지 마세요.
3. 표가 있다면 반드시 <table>, <thead>, <tbody>, <tr>, <th>, <td> 태그를 사용하세요. | 파이프 문자로 만든 마크다운 표는 사용하지 마세요.
4. 강조가 필요한 텍스트는 <strong> 또는 <em> 태그를 사용하세요. ** 또는 * 같은 마크다운 문법은 사용하지 마세요.
5. 코드나 특수 형식은 <code> 또는 <pre> 태그를 사용하세요.
6. 단락은 <p> 태그로 감싸세요.
7. 원본의 구조와 형식을 최대한 유지하되, **오직 HTML 문법으로만** 표현하세요.
8. 추출된 텍스트만 반환하고, 설명이나 주석은 포함하지 마세요.
9. HTML 태그는 올바르게 닫아주세요.
10. 반환 형식 예시: <h2>제목</h2><p>내용</p><table><tr><td>셀</td></tr></table>"""

    def _get_table_ocr_prompt(self) -> str:
        """표 전용 OCR 프롬프트 (표가 감지된 경우)."""
        return """이 이미지에서 모든 텍스트를 정확하게 추출하여 **반드시 HTML 형식으로만** 반환해주세요.
이 이미지에는 표(table)가 포함되어 있습니다. 표 구조를 정확히 인식하는 것이 매우 중요합니다.

**중요: 마크다운 문법(#, ##, |, -, 등)을 절대 사용하지 마세요. 오직 HTML 태그만 사용하세요.**

요구사항:
1. 제목은 반드시 <h1>, <h2>, <h3> 등의 HTML 헤더 태그를 사용하세요. # 또는 ## 같은 마크다운 문법은 사용하지 마세요.
2. 목록은 반드시 <ul>, <ol>, <li> 태그를 사용하세요. - 또는 1. 같은 마크다운 문법은 사용하지 마세요.
3. 표 처리 (매우 중요):
   - 표가 있다면 반드시 HTML <table> 태그를 사용하세요. | 파이프 문자로 만든 마크다운 표는 절대 사용하지 마세요.
   - 표 구조: <table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table>
   - 헤더 행은 <thead>와 <th> 태그를 사용하세요.
   - 데이터 행은 <tbody>와 <td> 태그를 사용하세요.
   - 빈 셀은 <td></td> 또는 <td> </td>로 표현하세요.
   - 표 안에 표가 있는 경우(중첩 표), 외부 표의 <td> 안에 내부 <table> 태그를 중첩하여 표현하세요.
   - 표의 행과 열 구조를 정확히 파악하여 변환하세요.
   - 병합된 셀은 colspan 또는 rowspan 속성을 사용하세요 (예: <td colspan="2">...</td>).
   - 표의 모든 셀에 실제 내용이 있는지 확인하고, 빈 셀은 공백으로 표시하세요.
   - 표의 헤더 행을 <thead>와 <th>로 명확히 구분하세요.
4. 강조가 필요한 텍스트는 <strong> 또는 <em> 태그를 사용하세요. ** 또는 * 같은 마크다운 문법은 사용하지 마세요.
5. 코드나 특수 형식은 <code> 또는 <pre> 태그를 사용하세요.
6. 단락은 <p> 태그로 감싸세요.
7. 원본의 구조와 형식을 최대한 유지하되, **오직 HTML 문법으로만** 표현하세요.
8. 추출된 텍스트만 반환하고, 설명이나 주석은 포함하지 마세요.
9. 표의 모든 행과 열을 빠짐없이 추출하세요.
10. HTML 태그는 올바르게 닫아주세요.
11. 반환 형식 예시: <h2>제목</h2><p>내용</p><table><thead><tr><th>헤더1</th><th>헤더2</th></tr></thead><tbody><tr><td>데이터1</td><td>데이터2</td></tr></tbody></table>"""

    def _detect_table(self, image_base64: str, server_process=None) -> bool:
        """
        이미지에서 표가 있는지 감지.
        
        Args:
            image_base64: base64 인코딩된 이미지
            server_process: llama-server 프로세스 (이미 시작된 경우, None이면 자동 시작)
            
        Returns:
            표가 있으면 True, 없으면 False
        """
        prompt = """이 이미지에 표(table)가 있는지 확인해주세요.
표는 행과 열로 구성된 격자 형태의 구조입니다.

응답 형식:
- 표가 있으면: "YES"
- 표가 없으면: "NO"
- 확실하지 않으면: "NO"

표가 있는지 여부만 간단히 답변하세요."""
        
        try:
            response = self._call_llm_api(prompt, image_base64, server_process=server_process)
            # 응답을 소문자로 변환하여 확인
            response_lower = response.strip().lower()
            # "yes" 또는 "표" 등의 키워드가 포함되어 있으면 표가 있다고 판단
            return "yes" in response_lower or "표" in response_lower
        except Exception as e:
            logger.warning(f"Table detection failed, assuming no table: {e}")
            # 표 감지 실패 시 기본값으로 False 반환 (기본 해상도 사용)
            return False

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
            
            # 503 에러 및 연결 에러 발생 시 재시도 로직
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
                    # 재시도
                    logger.warning(
                        f"LLM API returned 503 (Service Unavailable), retrying in {retry_delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(retry_delay)
                    continue
                except (httpx.ReadError, httpx.ConnectError) as e:
                    # 연결 리셋 또는 연결 에러는 재시도 (llama-server가 응답 중 종료될 수 있음)
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"LLM API connection error (will retry): {e} "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(retry_delay)
                        continue
                    else:
                        error_msg = f"LLM API connection error after {max_retries} attempts: {e}"
                        logger.error(error_msg)
                        raise RuntimeError(
                            f"{error_msg}. Please ensure LLM API server is running at {self.settings.llm_api_base_url}"
                        ) from e
                except httpx.HTTPError as e:
                    # 기타 HTTP 에러는 재시도하지 않음
                    logger.error(f"HTTP error calling LLM API: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Error calling LLM API: {e}")
                    raise

    def process_document(self, file_path: Path, ocr_mode: OcrMode = "basic") -> dict[str, Any]:
        """
        문서 파일을 OCR 처리.
        
        Args:
            file_path: 처리할 파일 경로
            ocr_mode: OCR 처리 모드 ("basic" 또는 "docling")
        
        Returns:
            {
                "ocr_text": str,  # 추출된 텍스트 (LLM 요약용)
                "page_count": int,  # 페이지 수
                "ocr_metadata": dict,  # 메타데이터
                "html_content": str | None,  # HTML 콘텐츠 (뷰어용, docling 모드만)
            }
        """
        logger.info(f"Processing document: {file_path} with mode: {ocr_mode}")
        
        # Docling 모드인 경우
        if ocr_mode == "docling":
            if not DOCLING_AVAILABLE:
                logger.warning("Docling not available, falling back to basic mode")
                return self._process_with_qwen3vl(file_path)
            
            return self._process_with_docling(file_path)
        
        # 기본 모드 (Qwen3-VL)
        return self._process_with_qwen3vl(file_path)
    
    def _process_with_docling(self, file_path: Path) -> dict[str, Any]:
        """
        Docling을 사용한 문서 처리 (Two-Track Strategy).
        
        Returns:
            {
                "ocr_text": str,  # JSON 기반 텍스트 (LLM 요약용)
                "page_count": int,
                "ocr_metadata": dict,
                "html_content": str,  # HTML (뷰어용)
            }
        """
        logger.info(f"Processing document with Docling: {file_path}")
        
        try:
            # Docling DocumentConverter 초기화
            converter = DocumentConverter()
            
            # 문서 변환
            result = converter.convert(str(file_path))
            
            # HTML 추출 (뷰어용)
            html_content = result.document.export_to_html()
            logger.info(f"HTML content extracted: {len(html_content)} characters")
            
            # JSON/텍스트 추출 (LLM 요약용)
            # Docling의 export_to_markdown 사용 (가능한 경우)
            try:
                ocr_text = result.document.export_to_markdown()
                logger.info(f"Markdown content extracted: {len(ocr_text)} characters")
            except Exception as e:
                logger.warning(f"Failed to export to markdown: {e}, falling back to manual dict extraction")
                doc_dict = result.document.export_to_dict()
                ocr_text = self._extract_text_from_docling_dict(doc_dict)
            
            # 페이지 수 계산
            page_count = len(doc_dict.get("pages", [])) if "pages" in doc_dict else 1
            
            # 메타데이터 구성
            ocr_metadata = {
                "file_path": str(file_path),
                "file_type": file_path.suffix.lower(),
                "page_count": page_count,
                "processing_mode": "docling",
                "html_generated": True,
                "docling_metadata": {
                    "num_tables": len(doc_dict.get("tables", [])) if "tables" in doc_dict else 0,
                    "num_figures": len(doc_dict.get("figures", [])) if "figures" in doc_dict else 0,
                }
            }
            
            logger.info(f"Docling processing completed: {len(ocr_text)} chars text, {len(html_content)} chars HTML")
            
            return {
                "ocr_text": ocr_text,
                "page_count": page_count,
                "ocr_metadata": ocr_metadata,
                "html_content": html_content,
            }
        
        except Exception as e:
            logger.error(f"Docling processing failed: {e}, falling back to Qwen3-VL")
            # Docling 실패 시 Qwen3-VL로 fallback
            return self._process_with_qwen3vl(file_path)
    
    def _extract_text_from_docling_dict(self, doc_dict: dict) -> str:
        """
        Docling JSON에서 텍스트 추출 (LLM 요약용).
        
        표, 목록, 단락 등의 구조를 유지하면서 텍스트만 추출합니다.
        """
        texts = []
        
        # body.children에서 참조를 따라가며 텍스트 추출
        body = doc_dict.get("body", {})
        children = body.get("children", [])
        
        # texts 배열에서 텍스트 추출
        texts_dict = {item.get("self_ref", ""): item for item in doc_dict.get("texts", [])}
        tables_dict = {item.get("self_ref", ""): item for item in doc_dict.get("tables", [])}
        
        # body.children의 순서대로 처리
        for child_ref in children:
            ref_path = child_ref.get("$ref", "")
            
            # 텍스트 참조인 경우
            if ref_path in texts_dict:
                text_item = texts_dict[ref_path]
                text_content = text_item.get("text", "")
                label = text_item.get("label", "")
                
                if text_content:
                    # label에 따라 포맷팅
                    if label == "section_header":
                        level = text_item.get("level", 1)
                        header_tag = "#" * (level + 1)  # level 1 -> ##, level 2 -> ###
                        texts.append(f"\n{header_tag} {text_content}\n")
                    elif label == "page_footer" or label == "page_header":
                        # 페이지 헤더/푸터는 제외
                        continue
                    else:
                        texts.append(f"{text_content}\n")
            
            # 테이블 참조인 경우
            elif ref_path in tables_dict:
                table = tables_dict[ref_path]
                texts.append(f"\n\n## 표\n")
                
                # 테이블 데이터 추출
                table_data = table.get("data", {})
                table_cells = table_data.get("table_cells", [])
                
                if table_cells:
                    # 행과 열로 그룹화
                    rows_dict = {}
                    max_col = 0
                    for cell in table_cells:
                        row_idx = cell.get("start_row_offset_idx", 0)
                        col_idx = cell.get("start_col_offset_idx", 0)
                        col_span = cell.get("col_span", 1)
                        cell_text = cell.get("text", "").strip()
                        
                        if row_idx not in rows_dict:
                            rows_dict[row_idx] = {}
                        
                        # colspan 처리
                        for c in range(col_idx, col_idx + col_span):
                            rows_dict[row_idx][c] = cell_text
                            max_col = max(max_col, c)
                    
                    # 행 순서대로 정렬하여 텍스트로 변환
                    for row_idx in sorted(rows_dict.keys()):
                        row_cells = rows_dict[row_idx]
                        row_text = " | ".join([row_cells.get(col_idx, "") for col_idx in range(max_col + 1)])
                        texts.append(f"{row_text}\n")
        
        # body.children이 없으면 texts 배열 전체를 순서대로 처리
        if not children and "texts" in doc_dict:
            for text_item in doc_dict["texts"]:
                text_content = text_item.get("text", "")
                label = text_item.get("label", "")
                
                if text_content:
                    if label == "section_header":
                        level = text_item.get("level", 1)
                        header_tag = "#" * (level + 1)
                        texts.append(f"\n{header_tag} {text_content}\n")
                    elif label not in ("page_footer", "page_header"):
                        texts.append(f"{text_content}\n")
        
        # 텍스트가 없으면 원본 딕셔너리를 JSON 문자열로 변환 (fallback)
        if not texts:
            logger.warning("No text extracted from Docling dict, returning empty string")
            return ""
        
        return "".join(texts)
    
    def _process_with_qwen3vl(self, file_path: Path) -> dict[str, Any]:
        """
        Qwen3-VL을 사용한 기존 OCR 처리 방식.
        
        Returns:
            {
                "ocr_text": str,
                "page_count": int,
                "ocr_metadata": dict,
                "html_content": None,  # 기본 모드는 HTML 생성 안 함
            }
        """
        from ..worker.llamacpp_server_client import _llama_server_process
        
        logger.info(f"Processing document: {file_path}")
        
        images: list[Image.Image] = []
        page_count = 0
        
        # 파일 타입에 따라 이미지 로드
        if self._is_pdf(file_path):
            if not PYMUPDF_AVAILABLE:
                raise ImportError(
                    "PDF processing requires PyMuPDF. "
                    "Install with: pip install PyMuPDF"
                )
            images = self._pdf_to_images(file_path)
            page_count = len(images)
        elif self._is_image(file_path):
            images = [self._load_image(file_path)]
            page_count = 1
        elif self._is_txt(file_path):
            # 텍스트 파일은 직접 읽어서 처리
            with open(file_path, 'r', encoding='utf-8') as f:
                text_content = f.read()
            # 텍스트 파일은 1페이지로 처리
            images = []
            page_count = 1
            # 텍스트 내용을 메타데이터에 저장 (OCR 처리 없이 바로 반환)
            logger.info(f"Text file detected, reading directly: {file_path}")
            return {
                "ocr_text": text_content,
                "page_count": 1,
                "ocr_metadata": {
                    "file_path": str(file_path),
                    "file_type": file_path.suffix.lower(),
                    "page_count": 1,
                    "pages": [{
                        "page_number": 1,
                        "text_length": len(text_content),
                        "status": "success",
                        "note": "Direct text file read (no OCR)"
                    }]
                }
            }
        elif self._is_office_document(file_path):
            # Office 문서를 PDF로 변환 후 기존 PDF 처리 로직 사용
            logger.info(f"Converting Office document to PDF: {file_path}")
            try:
                # Office 문서를 PDF로 변환
                pdf_path = self._office_to_pdf(file_path)
                
                try:
                    # 변환된 PDF를 이미지로 변환 (기존 로직 재사용)
                    if not PYMUPDF_AVAILABLE:
                        raise ImportError(
                            "PDF processing requires PyMuPDF. "
                            "Install with: pip install PyMuPDF"
                        )
                    images = self._pdf_to_images(pdf_path)
                    page_count = len(images)
                finally:
                    # 임시 PDF 파일 삭제
                    try:
                        if pdf_path.exists():
                            pdf_path.unlink()
                            logger.debug(f"Cleaned up temporary PDF file: {pdf_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete temporary PDF file {pdf_path}: {e}")
                
            except Exception as e:
                error_msg = f"Failed to convert Office document to PDF: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e
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
                
                image_base64 = None
                try:
                    # 1단계: 표 감지를 위한 기본 해상도 이미지 생성
                    detection_image_base64 = self._image_to_base64(image, max_size=(1536, 1536), quality=75)
                    
                    # 표 감지
                    has_table = self._detect_table(detection_image_base64, server_process=server_process)
                    logger.info(f"Page {page_idx + 1} table detection: {'Table found' if has_table else 'No table'}")
                    
                    # 2단계: 표 감지 결과에 따라 해상도 결정
                    if has_table:
                        # 표가 있으면 높은 해상도 사용 (표 인식 정확도 향상)
                        max_size = (2560, 2560)
                        quality = 85  # 표 인식을 위해 품질도 약간 증가
                        logger.info(f"Page {page_idx + 1}: Using high resolution ({max_size}) for table detection")
                    else:
                        # 표가 없으면 기본 해상도 사용 (메모리 및 처리 시간 최적화)
                        max_size = (1536, 1536)
                        quality = 75
                        logger.info(f"Page {page_idx + 1}: Using default resolution ({max_size})")
                    
                    # 최종 OCR용 이미지 생성
                    image_base64 = self._image_to_base64(image, max_size=max_size, quality=quality)
                    
                    # 표 감지 결과에 따라 프롬프트 선택
                    if has_table:
                        prompt = self._get_table_ocr_prompt()
                    else:
                        prompt = self._get_default_ocr_prompt()
                    
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
                finally:
                    # 메모리 정리: 이미지 데이터 즉시 해제
                    import gc
                    try:
                        image.close()
                    except Exception:
                        pass
                    # base64 데이터 해제
                    image_base64 = None
                    # 명시적 가비지 컬렉션으로 메모리 해제 촉진
                    # Vision Encoder 처리 후 메모리 정리를 위해 필요
                    gc.collect()
        
        # 모든 페이지 텍스트 결합 (마크다운 형식)
        # 페이지 구분은 마크다운 구분선과 페이지 번호 헤더 사용
        if len(all_texts) > 1:
            # 여러 페이지인 경우 페이지 번호 헤더 추가
            combined_texts = []
            for idx, text in enumerate(all_texts, 1):
                if text.strip():  # 빈 텍스트가 아닌 경우만 추가
                    combined_texts.append(f"## 페이지 {idx}\n\n{text}")
            ocr_text = "\n\n---\n\n".join(combined_texts)
        else:
            # 단일 페이지인 경우 페이지 번호 헤더 없이 반환
            ocr_text = all_texts[0] if all_texts else ""
        
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
        
        # processing_mode 추가
        ocr_metadata["processing_mode"] = "basic"
        
        return {
            "ocr_text": ocr_text,
            "page_count": page_count,
            "ocr_metadata": ocr_metadata,
            "html_content": None,  # 기본 모드는 HTML 생성 안 함
        }

