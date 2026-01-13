"""OCR Vision 모듈 - Qwen3-VL을 사용한 이미지 OCR.

이미지에서 텍스트를 추출하는 GPU 작업을 담당합니다.
이미지 전처리(PDF → 이미지 변환 등)는 백엔드에서 수행합니다.
"""

import base64
import re
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

    def __init__(self, ocr_provider: str | None = None):
        self.settings = get_settings()
        # ocr_provider가 지정되면 임시로 오버라이드 (이 작업에만 적용)
        if ocr_provider is not None:
            self._ocr_provider_override = ocr_provider
        else:
            self._ocr_provider_override = None

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
        """기본 OCR 프롬프트.
        
        System message에서 형식 규칙을 정의하므로, 여기서는 구체적인 작업 지시만 포함합니다.
        """
        return """이 이미지에서 모든 텍스트를 정확하게 추출해주세요.

요구사항:
1. 제목은 <h1>, <h2>, <h3> 등 HTML 헤더 태그 사용
2. 목록은 <ul>, <ol>, <li> 태그 사용
3. 표는 <table>, <thead>, <tbody>, <tr>, <th>, <td> 태그 사용
4. 강조는 <strong> 또는 <em> 태그 사용
5. 단락은 <p> 태그로 감싸기
6. 추출된 텍스트만 반환하고 설명은 포함하지 마세요"""

    def _get_table_ocr_prompt(self) -> str:
        """표 전용 OCR 프롬프트.
        
        System message에서 형식 규칙을 정의하므로, 여기서는 표 처리 요구사항만 포함합니다.
        """
        return """이 이미지에는 표(table)가 포함되어 있습니다. 표 구조를 정확히 인식하는 것이 매우 중요합니다.

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

    def _get_ocr_system_message(self) -> str:
        """OCR용 system message.
        
        LLM의 역할과 응답 형식을 명확히 정의하여 마크다운 코드 블록 없이
        순수 HTML만 반환하도록 지시합니다.
        """
        return """당신은 전문 OCR(광학 문자 인식) 시스템입니다.

**응답 형식 규칙 (절대 준수):**
1. 응답은 순수 HTML 태그만 사용하세요.
2. 마크다운 코드 블록(```html, ``` 등)을 절대 사용하지 마세요.
3. 응답 시작과 끝에 ``` 같은 마크다운 문법을 사용하지 마세요.
4. HTML 태그만 직접 반환하세요. 설명이나 주석을 추가하지 마세요.

**언어 규칙:**
- 한국어와 영어만 인식하고 반환하세요.
- 중국어는 절대 출력하지 마세요.
- 한자는 제한적으로만 사용하세요.

**HTML 태그 사용 규칙:**
- 제목: <h1>, <h2>, <h3> 등
- 단락: <p>
- 목록: <ul>, <ol>, <li>
- 표: <table>, <thead>, <tbody>, <tr>, <th>, <td>
- 강조: <strong>, <em>"""

    def _remove_markdown_code_blocks(self, content: str) -> str:
        """마크다운 코드 블록을 제거하고 내부 내용만 추출.
        
        FLM 등 일부 LLM이 응답을 마크다운 코드 블록(```html ... ```)으로 감싸서
        반환하는 경우를 처리합니다.
        
        Args:
            content: 원본 응답 텍스트
            
        Returns:
            마크다운 코드 블록이 제거된 텍스트
        """
        if not content:
            return content
        
        # 마크다운 코드 블록 패턴: ```[language]? ... ```
        # 예: ```html ... ``` 또는 ``` ... ```
        pattern = r'```[a-zA-Z]*\n(.*?)```'
        
        # 모든 코드 블록 찾기
        matches = re.finditer(pattern, content, re.DOTALL)
        
        if not matches:
            # 코드 블록이 없으면 원본 반환
            return content
        
        # 코드 블록이 있는 경우, 각 블록의 내용을 추출하여 결합
        cleaned_parts = []
        last_end = 0
        
        for match in matches:
            # 코드 블록 이전의 텍스트 추가
            if match.start() > last_end:
                cleaned_parts.append(content[last_end:match.start()])
            
            # 코드 블록 내부 내용 추가
            code_content = match.group(1).strip()
            if code_content:
                cleaned_parts.append(code_content)
            
            last_end = match.end()
        
        # 마지막 코드 블록 이후의 텍스트 추가
        if last_end < len(content):
            cleaned_parts.append(content[last_end:])
        
        result = '\n'.join(cleaned_parts).strip()
        
        # 결과가 비어있으면 원본 반환 (코드 블록만 있었던 경우)
        if not result:
            return content
        
        return result

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
        
        # ocr_provider 오버라이드 확인
        current_ocr_provider = self._ocr_provider_override if self._ocr_provider_override is not None else self.settings.ocr_provider
        logger.debug(f"[OCR Vision] _call_llm_api: ocr_provider_override={self._ocr_provider_override}, settings.ocr_provider={self.settings.ocr_provider}, current_ocr_provider={current_ocr_provider}")
        
        if server_process is None:
            # OCR은 ocr_provider를 사용 (기본값: llamacpp_server)
            # OCR provider가 llamacpp_server일 때만 서버 시작
            if current_ocr_provider == "llamacpp_server":
                # OCR에서 llamacpp_server 사용 시 ocr_provider 파라미터 전달
                server_context = _llama_server_process(self.settings, ocr_provider=current_ocr_provider)
            else:
                # FLM 등 외부 서버 사용 시 서버 시작 불필요
                server_context = nullcontext(None)
        else:
            server_context = nullcontext(server_process)
        
        with server_context:
            # current_ocr_provider에 따라 API URL과 모델 결정
            import os
            if current_ocr_provider == "flm":
                api_base_url = os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
                api_model_name = os.getenv("FLM_OCR_MODEL", "qwen3vl-it:4b")
                logger.info(f"[OCR Vision] Using FLM provider: url={api_base_url}, model={api_model_name}")
            else:
                if server_process and current_ocr_provider == "llamacpp_server":
                    api_base_url = f"http://localhost:{self.settings.ocr_server_port}"
                else:
                    api_base_url = f"http://localhost:{self.settings.llm_server_port}"
                
                api_model_name = self.settings.llm_model_name or "default"
                logger.info(f"[OCR Vision] Using llamacpp_server provider: url={api_base_url}, model={api_model_name}")
            
            url = f"{api_base_url}/v1/chat/completions"
            
            # System message로 응답 형식 명확히 정의
            messages = [
                {"role": "system", "content": self._get_ocr_system_message()}
            ]
            
            # User message에 실제 작업 지시
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
                "model": api_model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4096,
            }
            
            # OCR provider에 따라 Authorization 헤더 추가
            headers = {}
            if current_ocr_provider == "flm":
                headers["Authorization"] = "Bearer flm"
            
            max_retries = 3
            retry_delay = 3
            
            for attempt in range(max_retries):
                try:
                    with httpx.Client(timeout=300.0, headers=headers) as client:
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
                            # FLM 등이 마크다운 코드 블록으로 감싸서 반환하는 경우 처리
                            cleaned_content = self._remove_markdown_code_blocks(content)
                            return cleaned_content.strip()
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
        from worker.utils.semaphore import WorkerSemaphore
        
        logger.info(f"Processing {len(images)} images for OCR")
        
        
        # ocr_provider 오버라이드 확인
        current_ocr_provider = self._ocr_provider_override if self._ocr_provider_override is not None else self.settings.ocr_provider
        logger.info(f"[OCR Vision] process_images: ocr_provider_override={self._ocr_provider_override}, settings.ocr_provider={self.settings.ocr_provider}, current_ocr_provider={current_ocr_provider}")

        # OCR provider에 따라 세마포어 키 결정
        # llamacpp_server (GPU 모드) -> gpu, flm (NPU 모드, speed) -> npu
        semaphore_key = "gpu" if current_ocr_provider == "llamacpp_server" else "npu"
        logger.info(f"[OCR Vision] Using semaphore: worker:{semaphore_key}:active for provider={current_ocr_provider}")

        # OCR 작업 중 해당 리소스를 '사용 중'으로 표시하여 채팅 트래픽을 우회 유도
        with WorkerSemaphore(semaphore_key):
            page_texts: list[str] = []
            ocr_metadata: dict[str, Any] = {
                "page_count": len(images),
                "processing_mode": ocr_mode,
                "pages": []
            }
            
            # OCR provider에 따라 서버 시작 (llamacpp_server일 때만)
            from contextlib import nullcontext
            from worker.pipelines.llm.llamacpp_client import _llama_server_process
            
            if current_ocr_provider == "llamacpp_server":
                # OCR에서 llamacpp_server 사용 시 ocr_provider 파라미터 전달 및 OCR 전용 포트 사용
                server_context = _llama_server_process(self.settings, ocr_provider=current_ocr_provider, port=self.settings.ocr_server_port)
            else:
                # FLM 등 외부 서버 사용 시 서버 시작 불필요
                server_context = nullcontext(None)
            
            with server_context as server_process:
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
