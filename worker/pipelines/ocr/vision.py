"""OCR Vision 모듈 - Qwen3-VL을 사용한 이미지 OCR.

이미지에서 텍스트를 추출하는 GPU 작업을 담당합니다.
이미지 전처리(PDF → 이미지 변환 등)는 백엔드에서 수행합니다.
"""

import base64
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import httpx
from PIL import Image

from worker.config import get_settings
from worker.logging_config import logger

OcrMode = Literal["document", "portray"]


class OcrVisionProcessor:
    """OCR Vision 처리기 (워커용).
    
    이미지를 받아서 LLM Vision API로 텍스트를 추출합니다.
    """

    def __init__(self):
        self.settings = get_settings()

    def _image_to_base64(self, image: Image.Image, max_size: tuple[int, int] = (2048, 2048), quality: int = 85) -> str:
        """이미지를 base64 문자열로 변환."""
        import gc
        
        buffered = BytesIO()
        try:
            if image.mode != "RGB":
                image = image.convert("RGB")
            
            original_size = image.size
            if original_size[0] > max_size[0] or original_size[1] > max_size[1]:
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                logger.debug(f"Image resized from {original_size} to {image.size}")
            
            image.save(buffered, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buffered.getvalue()).decode()
        finally:
            buffered.close()
            gc.collect()

    def _get_default_ocr_prompt(self) -> str:
        """기본 OCR 프롬프트."""
        return """이 이미지에서 모든 텍스트를 정확하게 추출하여 **반드시 HTML 형식으로만** 반환해주세요.

**중요: 언어 준수 사항**
1. 이 문서는 **한국어(Korean)** 또는 **영어(English)** 문서입니다.
2. **절대로 중국어를 출력하지 마세요.**
3. 한자가 명확히 포함된 경우에만 제한적으로 표시하세요.

**중요: 마크다운 문법을 사용하지 마세요. 오직 HTML 태그만 사용하세요.**

요구사항:
1. 제목은 <h1>, <h2>, <h3> 등 HTML 헤더 태그 사용
2. 목록은 <ul>, <ol>, <li> 태그 사용
3. 표는 <table>, <thead>, <tbody>, <tr>, <th>, <td> 태그 사용
4. 강조는 <strong> 또는 <em> 태그 사용
5. 단락은 <p> 태그로 감싸기
6. 추출된 텍스트만 반환하고 설명은 포함하지 마세요"""

    def _get_table_ocr_prompt(self) -> str:
        """표 전용 OCR 프롬프트."""
        return """이 이미지에서 모든 텍스트를 정확하게 추출하여 **반드시 HTML 형식으로만** 반환해주세요.
이 이미지에는 표(table)가 포함되어 있습니다. 표 구조를 정확히 인식하는 것이 매우 중요합니다.

**중요: 언어 준수 사항**
1. 이 문서는 **한국어(Korean)** 또는 **영어(English)** 문서입니다.
2. **절대로 중국어를 출력하지 마세요.**

**중요: 마크다운 문법을 사용하지 마세요. 오직 HTML 태그만 사용하세요.**

표 처리 요구사항:
1. 표는 반드시 HTML <table> 태그 사용
2. 표 구조: <table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table>
3. 헤더 행은 <thead>와 <th> 태그 사용
4. 데이터 행은 <tbody>와 <td> 태그 사용
5. 병합된 셀은 colspan 또는 rowspan 속성 사용
6. 표의 모든 행과 열을 빠짐없이 추출하세요"""

    def _get_portray_prompt(self) -> str:
        """이미지 묘사(Portray) 전용 프롬프트."""
        return """이 이미지를 전문가의 시각으로 상세하게 분석하고 묘사해주세요.

**응답 형식**: 반드시 HTML 형식으로 작성해주세요.

**구조 가이드**:
1. **<h2>전반적인 상황</h2>**: 이미지의 분위기, 배경, 시간대 등 묘사
2. **<h2>주요 대상 및 인물</h2>**: 등장인물의 외양, 표정, 행동 등 상세 묘사
3. **<h2>세부 특징 및 맥락</h2>**: 흥미로운 세부사항이나 맥락 추론

**언어**: 한국어로 작성
**스타일**: 객관적이고 전문적인 어조, 구체적이고 생생하게 묘사"""

    def _detect_table(self, image_base64: str, server_process=None) -> bool:
        """이미지에서 표 존재 여부 감지."""
        prompt = """이 이미지에 표(table)가 있는지 확인해주세요.
표는 행과 열로 구성된 격자 형태의 구조입니다.

응답 형식:
- 표가 있으면: "YES"
- 표가 없으면: "NO"
- 확실하지 않으면: "NO"

표가 있는지 여부만 간단히 답변하세요."""
        
        try:
            response = self._call_llm_api(prompt, image_base64, server_process=server_process)
            response_lower = response.strip().lower()
            return "yes" in response_lower or "표" in response_lower
        except Exception as e:
            logger.warning(f"Table detection failed, assuming no table: {e}")
            return False

    def _call_llm_api(self, prompt: str, image_base64: str | None = None, server_process=None) -> str:
        """LLM API를 호출하여 OCR 수행."""
        from contextlib import nullcontext
        from worker.pipelines.llm.llamacpp_client import _llama_server_process
        
        if server_process is None:
            server_context = _llama_server_process(self.settings)
        else:
            server_context = nullcontext(server_process)
        
        with server_context:
            url = f"{self.settings.llm_api_base_url}/v1/chat/completions"
            
            messages = []
            if image_base64:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                        },
                        {"type": "text", "text": prompt}
                    ]
                })
            else:
                messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.settings.llm_api_model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4096,
            }
            
            max_retries = 3
            retry_delay = 3
            
            for attempt in range(max_retries):
                try:
                    with httpx.Client(timeout=300.0) as client:
                        response = client.post(url, json=payload)
                        
                        if response.status_code == 503:
                            if attempt < max_retries - 1:
                                logger.warning(f"LLM API 503, retrying in {retry_delay}s ({attempt + 1}/{max_retries})")
                                time.sleep(retry_delay)
                                continue
                            response.raise_for_status()
                        
                        response.raise_for_status()
                        result = response.json()
                        
                        if "choices" in result and len(result["choices"]) > 0:
                            content = result["choices"][0].get("message", {}).get("content", "")
                            return content.strip()
                        else:
                            raise ValueError(f"Unexpected API response: {result}")
                            
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 503:
                        logger.error(f"HTTP error calling LLM API: {e}")
                        raise
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(retry_delay)
                except (httpx.ReadError, httpx.ConnectError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"LLM API connection error, retrying: {e}")
                        time.sleep(retry_delay)
                        continue
                    raise RuntimeError(f"LLM API connection error: {e}") from e
                except httpx.HTTPError as e:
                    logger.error(f"HTTP error calling LLM API: {e}")
                    raise
        
        raise RuntimeError("LLM API call failed after all retries")

    def process_image(
        self,
        image: Image.Image,
        ocr_mode: OcrMode = "document",
        server_process=None,
    ) -> str:
        """단일 이미지를 OCR 처리.
        
        Args:
            image: PIL Image 객체
            ocr_mode: OCR 모드 ("document" 또는 "portray")
            server_process: 이미 시작된 llama-server 프로세스
            
        Returns:
            추출된 텍스트
        """
        import gc
        
        try:
            has_table = False
            
            if ocr_mode != "portray":
                # 표 감지를 위한 저해상도 이미지
                detection_base64 = self._image_to_base64(image, max_size=(1536, 1536), quality=75)
                has_table = self._detect_table(detection_base64, server_process=server_process)
                logger.info(f"Table detection: {'found' if has_table else 'not found'}")
            
            # 표 여부에 따라 해상도 결정
            if has_table:
                max_size = (2560, 2560)
                quality = 85
            else:
                max_size = (1536, 1536)
                quality = 75
            
            image_base64 = self._image_to_base64(image, max_size=max_size, quality=quality)
            
            # 프롬프트 선택
            if ocr_mode == "portray":
                prompt = self._get_portray_prompt()
            elif has_table:
                prompt = self._get_table_ocr_prompt()
            else:
                prompt = self._get_default_ocr_prompt()
            
            # LLM API 호출
            text = self._call_llm_api(prompt, image_base64, server_process=server_process)
            return text
            
        finally:
            gc.collect()

    def process_images(
        self,
        images: list[Image.Image],
        ocr_mode: OcrMode = "document",
    ) -> dict[str, Any]:
        """여러 이미지를 OCR 처리.
        
        Args:
            images: PIL Image 객체 목록
            ocr_mode: OCR 모드
            
        Returns:
            {
                "ocr_text": str,      # 전체 텍스트
                "page_count": int,    # 페이지 수
                "page_texts": list,   # 페이지별 텍스트
                "ocr_metadata": dict, # 메타데이터
            }
        """
        from worker.pipelines.llm.llamacpp_client import _llama_server_process
        
        logger.info(f"Processing {len(images)} images for OCR")
        
        page_texts: list[str] = []
        ocr_metadata: dict[str, Any] = {
            "page_count": len(images),
            "processing_mode": ocr_mode,
            "pages": []
        }
        
        # llama-server를 한 번만 시작
        with _llama_server_process(self.settings) as server_process:
            for idx, image in enumerate(images):
                page_num = idx + 1
                logger.info(f"Processing page {page_num}/{len(images)}")
                
                try:
                    text = self.process_image(image, ocr_mode=ocr_mode, server_process=server_process)
                    page_texts.append(text)
                    
                    ocr_metadata["pages"].append({
                        "page_number": page_num,
                        "text_length": len(text),
                        "status": "success"
                    })
                    logger.info(f"Page {page_num} completed ({len(text)} chars)")
                    
                except Exception as e:
                    logger.error(f"Failed to process page {page_num}: {e}")
                    page_texts.append("")
                    ocr_metadata["pages"].append({
                        "page_number": page_num,
                        "status": "failed",
                        "error": str(e)
                    })
                    
                    # 첫 페이지 실패는 치명적
                    if idx == 0:
                        raise RuntimeError(f"OCR failed for first page: {e}") from e
                
                finally:
                    try:
                        image.close()
                    except Exception:
                        pass
        
        # 텍스트 결합
        if len(page_texts) > 1:
            combined_texts = []
            for idx, text in enumerate(page_texts, 1):
                if text.strip():
                    combined_texts.append(f"## 페이지 {idx}\n\n{text}")
            ocr_text = "\n\n---\n\n".join(combined_texts)
        else:
            ocr_text = page_texts[0] if page_texts else ""
        
        # 모든 페이지 실패 체크
        if not ocr_text.strip():
            failed_pages = [p for p in ocr_metadata["pages"] if p.get("status") == "failed"]
            if failed_pages:
                errors = [p.get("error", "Unknown") for p in failed_pages[:3]]
                raise RuntimeError(f"All pages OCR failed: {'; '.join(errors)}")
        
        return {
            "ocr_text": ocr_text,
            "page_count": len(images),
            "page_texts": page_texts,
            "ocr_metadata": ocr_metadata,
        }
