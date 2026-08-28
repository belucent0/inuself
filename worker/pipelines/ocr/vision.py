"""OCR Vision 모듈 - 이미지 OCR.

이미지에서 텍스트를 추출하는 OCR 작업을 담당합니다.
이미지 전처리(PDF → 이미지 변환 등)는 백엔드에서 수행합니다.

현행: Worker → ai-gateway → ai-llm(Gemma 4 26B A4B)로 OCR 요청.
"""

import base64
import json
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import httpx
import redis
from PIL import Image

from worker.logging_config import logger

AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://ai-gateway:4000")
AI_GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "")
OCR_REQUEST_TIMEOUT = 300.0  # 5분


def _call_ocr_via_ai_gateway(
    image_base64: str,
    prompt: str,
    accuracy_mode: str = "speed",
    timeout: float = OCR_REQUEST_TIMEOUT,
    on_processing_started: callable = None,
    file_id: str = None,
) -> str:
    """ai-gateway를 통해 OCR 요청.

    Worker → ai-gateway → ai-llm(Gemma 4 26B A4B)로 호출합니다.

    Args:
        image_base64: Base64 인코딩된 이미지
        prompt: OCR 프롬프트
        accuracy_mode: "speed" 또는 "accuracy" — 이미지 전처리 품질 힌트
        timeout: 타임아웃 (초)
        on_processing_started: 처리 시작 시 호출될 콜백
        file_id: 파일 ID (Backend 상태 업데이트용)

    Returns:
        OCR 결과 텍스트
    """
    logger.info(
        "[OCR Vision] Sending OCR via AI Gateway: model=gemma4-a4b, "
        f"accuracy_mode={accuracy_mode}"
    )

    # OpenAI Vision API 형식으로 요청
    url = f"{AI_GATEWAY_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_GATEWAY_API_KEY}",
        "Content-Type": "application/json",
    }
    
    # OpenTelemetry trace context 주입 (Worker→AI Gateway 연결)
    try:
        from worker.telemetry import inject_trace_context
        logger.info(f"[Telemetry Debug] Headers before injection: {headers}")
        inject_trace_context(headers)
        logger.info(f"[Telemetry Debug] Headers after injection: {headers}")
    except Exception as e:
        logger.error(f"[Telemetry Debug] Failed to inject trace context: {e}")
        pass  # telemetry 미초기화 시 무시

    extra_body = {
        "accuracy_mode": accuracy_mode,
        "task_type": "ocr",
    }
    if file_id:
        extra_body["file_id"] = file_id

    payload = {
        "model": "gemma4-a4b",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                ],
            }
        ],
        "max_tokens": 8192,
        "temperature": 0.1,
        # custom_handler가 OCR로 라우팅하도록 힌트
        "extra_body": extra_body,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()

            # OpenAI 형식 응답에서 텍스트 추출
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0].get("message", {}).get("content", "")
                logger.info(f"[OCR Vision] OCR completed via AI Gateway")
                return content.strip()

            raise ValueError(f"Unexpected OCR response format: {result}")

    except httpx.HTTPStatusError as e:
        logger.error(f"[OCR Vision] AI Gateway HTTP error: {e.response.status_code} - {e.response.text}")
        raise RuntimeError(f"OCR HTTP error: {e.response.status_code}")
    except httpx.TimeoutException:
        raise TimeoutError(f"OCR timeout after {timeout}s")
    except Exception as e:
        logger.error(f"[OCR Vision] AI Gateway request failed: {e}")
        raise RuntimeError(f"OCR request failed: {e}")


OcrMode = Literal["document", "portray"]


class OcrVisionProcessor:
    """OCR Vision 처리기 (워커용).
    
    이미지를 받아서 LLM Vision API로 텍스트를 추출합니다.
    """

    def __init__(self, accuracy_mode: str = "speed"):
        self.accuracy_mode = accuracy_mode

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
        return """이 이미지의 **모든 텍스트**를 위에서 아래로 순서대로 추출해주세요.

**중요**: 이미지 상단부터 하단까지 모든 내용을 빠짐없이 추출하세요.

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
        return """이 이미지의 **모든 텍스트**를 위에서 아래로 순서대로 추출해주세요.

**중요**: 이미지 상단부터 하단까지 모든 내용을 빠짐없이 추출하세요.
- 제목, 날짜, 금액, 설명 텍스트 등 표 외의 내용도 모두 포함
- 표가 있다면 표 구조도 정확히 추출

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

    def _detect_table(self, image_base64: str) -> bool:
        """이미지에서 표 존재 여부 감지."""
        prompt = """이 이미지에 표(table)가 있는지 확인해주세요.
표는 행과 열로 구성된 격자 형태의 구조입니다.

응답 형식:
- 표가 있으면: "YES"
- 표가 없으면: "NO"
- 확실하지 않으면: "NO"

표가 있는지 여부만 간단히 답변하세요."""
        
        try:
            response = self._call_llm_api(prompt, image_base64)
            response_lower = response.strip().lower()
            return "yes" in response_lower or "표" in response_lower
        except Exception as e:
            logger.warning(f"Table detection failed, assuming no table: {e}")
            return False

    def _call_llm_api(self, prompt: str, image_base64: str | None = None, file_id: str = None) -> str:
        """ai-gateway를 통해 OCR 요청.

        Worker → ai-gateway → ai-llm(Gemma 4 26B A4B)로 호출합니다.
        """
        if image_base64 is None:
            raise ValueError("OCR call requires image_base64")

        result = _call_ocr_via_ai_gateway(
            image_base64=image_base64,
            prompt=prompt,
            accuracy_mode=self.accuracy_mode,
            file_id=file_id,
        )
        return self._remove_markdown_code_blocks(result)

    def process_image(
        self,
        image: Image.Image,
        ocr_mode: OcrMode = "document",
        file_id: str = None,
    ) -> str:
        """단일 이미지를 OCR 처리.

        Args:
            image: PIL Image 객체
            ocr_mode: OCR 모드 ("document" 또는 "portray")
            file_id: 파일 ID (Backend 상태 업데이트용)

        Returns:
            추출된 텍스트
        """
        import gc

        try:
            has_table = False

            is_speed_mode = self.accuracy_mode == "speed"

            if ocr_mode != "portray":
                # 표 감지를 위한 저해상도 이미지 (speed: 1024x1024, accuracy: 1536x1536)
                detection_size = (1024, 1024) if is_speed_mode else (1536, 1536)
                detection_base64 = self._image_to_base64(image, max_size=detection_size, quality=70)
                has_table = self._detect_table(detection_base64)
                logger.info(f"Table detection: {'found' if has_table else 'not found'}")

            # 표 여부에 따라 해상도 결정 (speed: 메모리 제한으로 더 작은 크기)
            if has_table:
                max_size = (1536, 1536) if is_speed_mode else (2560, 2560)
                quality = 80 if is_speed_mode else 85
            else:
                max_size = (1280, 1280) if is_speed_mode else (1536, 1536)
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
            text = self._call_llm_api(prompt, image_base64, file_id=file_id)
            return text
            
        finally:
            gc.collect()

    def process_images(
        self,
        images: list[Image.Image],
        ocr_mode: OcrMode = "document",
        resource_timeout: float = 120.0,
        file_id: str = None,
        on_progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, Any]:
        """여러 이미지를 OCR 처리.

        ai-gateway가 ai-llm(Gemma 4 26B A4B)로 라우팅합니다.

        Args:
            images: PIL Image 객체 목록
            ocr_mode: OCR 모드
            resource_timeout: (미사용, 호환성 유지)
            file_id: 파일 ID (Backend 상태 업데이트용)

        Returns:
            {
                "ocr_text": str,      # 전체 텍스트
                "page_count": int,    # 페이지 수
                "page_texts": list,   # 페이지별 텍스트
                "ocr_metadata": dict, # 메타데이터
            }
        """
        logger.info(f"Processing {len(images)} images for OCR")

        resource_type = "gpu"
        logger.info(
            f"[OCR Vision] Using resource: gpu/ocr, accuracy_mode={self.accuracy_mode}"
        )

        # 이미지 OCR 처리 (리소스 게이트 없이 직접 처리)
        page_texts: list[str] = []
        ocr_metadata: dict[str, Any] = {
            "page_count": len(images),
            "processing_mode": ocr_mode,
            "resource_type": resource_type,
            "resource_wait_time": 0.0,
            "pages": []
        }

        # ai-gateway가 ai-llm으로 라우팅하므로 host에서 서버 시작 불필요.
        for idx, image in enumerate(images):
            page_num = idx + 1
            logger.info(f"Processing page {page_num}/{len(images)}")

            try:
                # 첫 페이지만 file_id 전달 (processing_started는 한 번만)
                text = self.process_image(
                    image,
                    ocr_mode=ocr_mode,
                    file_id=file_id if idx == 0 else None,
                )
                page_texts.append(text)

                ocr_metadata["pages"].append({
                    "page_number": page_num,
                    "text_length": len(text),
                    "status": "success"
                })
                logger.info(f"Page {page_num} completed ({len(text)} chars)")

                # 페이지별 진행률 발행 (25% ~ 85% 구간에 매핑)
                if on_progress:
                    page_progress = 25 + (page_num / len(images)) * 60
                    on_progress(page_progress, f"페이지 {page_num}/{len(images)} 완료")

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
