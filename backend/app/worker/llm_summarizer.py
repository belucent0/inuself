from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..core.config import get_settings
from .lmstudio_client import LMStudioClientError, request_chat_completion

logger = logging.getLogger(__name__)

DEFAULT_SUMMARY_PROMPT = """당신은 회의록을 요약하는 전문가입니다. 주어진 회의록의 적절한 제목을 추출하고, 키워드와 요약을 생성하세요.

1. (필수) title: 주어진 회의록에 대한 적절한 제목을 생성하세요. 제목만 보고도 회의의 대략적인 내용을 추측할 수 있어야 합니다. 제목은 반드시 한글로 작성하세요.

2. summary: 다음 한국어 전사 내용을 간결한 마크다운 형식으로 요약해주세요.
   - 모든 내용은 반드시 한글로 작성하세요
   - `## 요약` 제목으로 시작하세요
   - 핵심 내용을 불릿 포인트로 제공하세요
   - `## 세부 사항` 섹션에 결정 사항이나 액션 아이템을 번호로 나열하세요
   - 화자 이름이 제공되면 보존하세요
   - 출력은 유효한 마크다운 형식이어야 합니다
   - 원본 전사 내용을 포함하지 마세요
   - 지시사항이나 프롬프트를 출력에 포함하지 마세요

---

다음 JSON 형식으로 반드시 응답하세요:

{{
    "title": "제목",  # 필수. 한글로 작성
    "summary": "## 요약\\n\\n- 내용...\\n\\n## 세부 사항\\n\\n1. 항목..."
}}

전사 내용:
{transcript}
"""


def summarize_transcription(text: str) -> tuple[str, str]:
    """
    전사 텍스트를 요약하고 제목을 추출합니다.
    
    Returns:
        (title, summary_md) 튜플
    """
    import json
    import re
    
    normalized = text.strip()
    if not normalized:
        raise ValueError("요약할 전사 텍스트가 비어 있습니다.")

    settings = get_settings()
    
    # LLM 호출
    if settings.llm_provider == "ollama":
        raw_response = _summarize_with_ollama(normalized, settings)
    elif settings.llm_provider == "llama_cpp":
        raw_response = _summarize_with_llama_cpp(normalized, settings)
    elif settings.llm_provider == "lmstudio":
        raw_response = _summarize_with_lmstudio(normalized, settings)
    else:
        raise ValueError(f"지원하지 않는 LLM provider: {settings.llm_provider}")
    
    # JSON 파싱 시도
    title, summary_md = _parse_json_response(raw_response, normalized)
    
    return title, summary_md


def _parse_json_response(raw_response: str, transcript_text: str) -> tuple[str, str]:
    """
    LLM 응답에서 JSON을 파싱하여 title과 summary를 추출합니다.
    JSON 파싱 실패 시 fallback 로직을 사용합니다.
    """
    import json
    import re
    
    # 1. ```json ... ``` 블록 찾기
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 2. ``` 없이 JSON만 있는 경우 - 중첩된 중괄호 처리
        # "title"과 "summary" 키가 모두 포함된 JSON 객체 찾기
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*"title"[^{}]*(?:\{[^{}]*\}[^{}]*)*"summary"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            # 3. 마지막 시도: 첫 번째 { 부터 마지막 } 까지 (중괄호 균형 맞추기)
            start_idx = raw_response.find('{')
            if start_idx >= 0:
                # 중괄호 균형을 맞춰서 끝 찾기
                brace_count = 0
                end_idx = start_idx
                for i in range(start_idx, len(raw_response)):
                    if raw_response[i] == '{':
                        brace_count += 1
                    elif raw_response[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i
                            break
                
                if end_idx > start_idx:
                    json_str = raw_response[start_idx:end_idx + 1]
                else:
                    logger.warning("JSON 형식을 찾을 수 없음, fallback 사용")
                    return _extract_title_fallback("", transcript_text), sanitize_summary_output(raw_response, transcript_text)
            else:
                logger.warning("JSON 형식을 찾을 수 없음, fallback 사용")
                return _extract_title_fallback("", transcript_text), sanitize_summary_output(raw_response, transcript_text)
    
    try:
        data = json.loads(json_str)
        title = str(data.get("title", "")).strip()
        summary_md = str(data.get("summary", "")).strip()
        
        # title이 비어있거나 유효하지 않으면 fallback 사용
        if not title or _looks_like_invalid_title(title):
            logger.warning("JSON에서 추출한 제목이 유효하지 않음: %s", title[:100])
            title = _extract_title_fallback(summary_md if summary_md else raw_response, transcript_text)
        
        # summary가 비어있으면 원본 응답 사용
        if not summary_md:
            logger.warning("JSON에서 summary가 비어있음, 원본 응답 사용")
            summary_md = raw_response
        
        return title, summary_md
        
    except json.JSONDecodeError as e:
        logger.warning("JSON 파싱 실패: %s, fallback 사용. JSON 문자열: %s", e, json_str[:200])
        # JSON 파싱 실패 시 원본 응답에서 제목과 요약 추출 시도
        return _extract_title_fallback(raw_response, transcript_text), sanitize_summary_output(raw_response, transcript_text)


def _summarize_with_ollama(text: str, settings) -> str:
    """Ollama API를 사용한 요약."""
    prompt = DEFAULT_SUMMARY_PROMPT.format(transcript=text)
    
    ollama_url = f"{settings.ollama_base_url}/api/generate"
    payload = {
        "model": settings.ollama_model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": settings.llm_temperature,
            "top_p": settings.llm_top_p,
            "num_predict": settings.llm_max_tokens,
            "num_gpu": -1,  # GPU 레이어 수 (-1 = 모든 레이어를 GPU에 로드)
            "num_ctx": settings.llm_context_length,
        },
    }
    
    logger.info("Ollama API 호출: model=%s, url=%s, num_gpu=%s", 
                settings.ollama_model_name, ollama_url, payload["options"].get("num_gpu"))
    
    try:
        with httpx.Client(timeout=600.0) as client:  # 10분 타임아웃 (대형 모델 로딩 시간 고려)
            # GPU 사용 강제: 이미 로드된 모델이 시스템 RAM에 있으면 GPU로 이동하지 않을 수 있음
            # keep_alive를 0으로 설정하여 요청 후 모델을 언로드하고, 다음 요청에서 재로드
            # 이렇게 하면 num_gpu=-1 옵션이 적용되어 GPU에 로드될 수 있음
            payload["keep_alive"] = "0"  # 요청 후 즉시 언로드 (다음 요청에서 재로드)
            
            response = client.post(ollama_url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            # GPU 사용 여부 확인을 위한 로그
            load_duration = result.get("load_duration", 0)
            total_duration = result.get("total_duration", 0)
            eval_duration = result.get("eval_duration", 0)
            logger.info(
                "Ollama 응답: load_duration=%.2fs, total_duration=%.2fs, eval_duration=%.2fs",
                load_duration / 1e9 if load_duration > 0 else 0,
                total_duration / 1e9 if total_duration > 0 else 0,
                eval_duration / 1e9 if eval_duration > 0 else 0,
            )
            
            raw_response = result.get("response", "").strip()
            if not raw_response:
                raise RuntimeError("LLM 요약 결과가 비어 있습니다.")
            
            logger.info("Ollama 응답 완료 (길이: %d chars)", len(raw_response))
            return raw_response
    except httpx.HTTPError as e:
        error_msg = f"Ollama API 호출 실패: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e
    except Exception as e:
        error_msg = f"Ollama 요약 처리 실패: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


def _summarize_with_llama_cpp(text: str, settings) -> str:
    """llama.cpp 직접 사용한 요약 (기존 방식)."""
    import threading
    from dataclasses import dataclass
    from functools import lru_cache
    from pathlib import Path
    from llama_cpp import Llama
    
    @dataclass(frozen=True)
    class SummarizerConfig:
        model_path: str
        context_length: int
        max_tokens: int
        temperature: float
        top_p: float
        n_threads: int
    
    _model_lock = threading.Lock()
    
    @lru_cache(maxsize=1)
    def _get_llama_model(config):
        """llama.cpp Llama 인스턴스를 캐시해 재사용한다 (llama_cpp provider용)."""
        from pathlib import Path
        from llama_cpp import Llama
        
        # 모델 파일 검증
        model_path = Path(config.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"모델 파일을 찾을 수 없습니다: {config.model_path}")
        if not model_path.is_file():
            raise ValueError(f"모델 경로가 파일이 아닙니다: {config.model_path}")
        
        logger.info("LLM 모델 로드 시작: %s", config.model_path)
        logger.info("Vulkan 가속 사용 (n_gpu_layers=-1, CPU 폴백 금지)")
        
        try:
            with _model_lock:
                llama = Llama(
                    model_path=config.model_path,
                    n_ctx=config.context_length,
                    n_threads=config.n_threads,
                    logits_all=False,
                    embedding=False,
                    use_mlock=True,
                    n_gpu_layers=-1,  # Vulkan만 사용, CPU 폴백 금지
                )
                logger.info("LLM 모델 로드 완료")
                return llama
        except Exception as e:
            error_msg = (
                f"LLM 모델 로드 실패: {e}\n"
                f"가능한 원인:\n"
                f"1. 모델 파일이 손상되었거나 호환되지 않는 형식\n"
                f"2. llama-cpp-python이 Vulkan을 제대로 지원하지 않는 빌드\n"
                f"3. 모델 형식이 llama-cpp-python 버전과 호환되지 않음\n"
                f"해결 방법:\n"
                f"- 모델 파일을 재다운로드하거나\n"
                f"- llama-cpp-python을 Vulkan 지원으로 재빌드:\n"
                f"  CMAKE_ARGS='-DGGML_VULKAN=ON' pip install --force-reinstall llama-cpp-python"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    cfg = SummarizerConfig(
        model_path=str(settings.llm_model_path),
        context_length=settings.llm_context_length,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        n_threads=settings.llm_n_threads,
    )
    
    prompt = DEFAULT_SUMMARY_PROMPT.format(transcript=text)
    llama = _get_llama_model(cfg)
    response = llama.create_completion(
        prompt=prompt,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
        top_p=cfg.top_p,
        stream=False,
        echo=False,
    )
    
    raw_response = _extract_text(response).strip()
    if not raw_response:
        raise RuntimeError("LLM 요약 결과가 비어 있습니다.")
    return raw_response


def _split_text_into_chunks(text: str, max_tokens_per_chunk: int = 10000, overlap_tokens: int = 500) -> list[str]:
    """
    텍스트를 지정된 토큰 수 단위로 청크로 분할합니다.
    
    Args:
        text: 분할할 텍스트
        max_tokens_per_chunk: 각 청크의 최대 토큰 수 (기본값: 10000)
        overlap_tokens: 청크 간 겹치는 토큰 수 (문맥 유지용, 기본값: 500)
    
    Returns:
        텍스트 청크 리스트
    """
    # 대략적인 변환: 1 토큰 ≈ 3-4 문자 (한국어는 더 많을 수 있음)
    # 안전하게 3.5 문자로 계산
    chars_per_token = 3.5
    max_chunk_chars = int(max_tokens_per_chunk * chars_per_token)
    overlap_chars = int(overlap_tokens * chars_per_token)
    
    if len(text) <= max_chunk_chars:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + max_chunk_chars
        
        # 문장 경계에서 자르기 (가능한 경우)
        if end < len(text):
            # 마지막 문장 끝(., !, ?, \n) 찾기
            last_period = text.rfind('.', start, end)
            last_newline = text.rfind('\n', start, end)
            last_exclamation = text.rfind('!', start, end)
            last_question = text.rfind('?', start, end)
            
            # 가장 가까운 문장 끝 찾기
            sentence_end = max(last_period, last_newline, last_exclamation, last_question)
            # 최소 70%는 유지 (너무 작은 청크 방지)
            if sentence_end > start + max_chunk_chars * 0.7:
                end = sentence_end + 1
        
        chunk = text[start:end]
        chunks.append(chunk)
        
        # 다음 청크 시작 위치 (overlap 고려)
        start = end - overlap_chars
        if start >= len(text):
            break
        if start < 0:
            start = 0
    
    return chunks


def _summarize_chunk_with_lmstudio(chunk: str, chunk_index: int, total_chunks: int, settings) -> str:
    """단일 청크를 LM Studio로 요약합니다."""
    prompt = DEFAULT_SUMMARY_PROMPT.format(transcript=chunk)
    system_prompt = settings.lmstudio_system_prompt.strip()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    try:
        raw_response = request_chat_completion(settings=settings, messages=messages, stream=False)
        raw_response = raw_response.strip()
        if not raw_response:
            raise RuntimeError("LM Studio 요약 결과가 비어 있습니다.")
        logger.info("청크 %d/%d 응답 완료 (길이: %d chars)", chunk_index, total_chunks, len(raw_response))
        return raw_response
    except LMStudioClientError as exc:
        error_str = str(exc)
        # 컨텍스트 길이 초과 에러는 재시도하지 않도록 특별 처리
        if "context" in error_str.lower() or "token" in error_str.lower() or "overflow" in error_str.lower():
            error_msg = f"LM Studio 컨텍스트 길이 초과 (청크 {chunk_index}/{total_chunks}): {exc}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from exc
        error_msg = f"LM Studio 요약 실패 (청크 {chunk_index}/{total_chunks}): {exc}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from exc


def _summarize_with_lmstudio(text: str, settings) -> str:
    """
    LM Studio Chat Completions API를 통한 요약.
    긴 텍스트는 청킹하여 처리합니다.
    """
    # 실제 사용 가능한 컨텍스트 길이 계산
    # 시스템 프롬프트 + 사용자 프롬프트 템플릿 + 응답 공간
    # LM Studio에서 실제 로드된 컨텍스트 길이를 고려
    # 프롬프트 템플릿 오버헤드: 시스템 프롬프트 + 프롬프트 템플릿 (약 200-300 토큰)
    # 안전 마진: max_tokens + 추가 오버헤드 (약 2000 토큰)
    # 실제 모델 컨텍스트가 15016 토큰이므로 더 보수적으로 계산
    actual_context = min(settings.llm_context_length, 15000)
    # 프롬프트 오버헤드를 더 크게 고려 (시스템 프롬프트 + 프롬프트 템플릿 + 안전 마진)
    prompt_overhead = 2500  # 시스템 프롬프트 + 프롬프트 템플릿 + 안전 마진
    available_tokens = actual_context - settings.llm_max_tokens - prompt_overhead
    
    # 한국어는 토큰 수가 더 많으므로 더 보수적인 변환 사용
    # 1 토큰 ≈ 2.5-3.0 문자 (한국어는 영어보다 토큰 수가 많음)
    chars_per_token = 2.5  # 더 보수적으로 2.5 문자/토큰 사용
    max_chunk_chars = int(available_tokens * chars_per_token)
    
    logger.info(
        "[LLM Summarizer] 텍스트 길이 확인: 전체=%d chars, 최대 청크=%d chars (약 %d 토큰), "
        "설정 컨텍스트=%d, 실제 사용 가능=%d 토큰, 프롬프트 오버헤드=%d 토큰",
        len(text), max_chunk_chars, available_tokens, settings.llm_context_length, available_tokens, prompt_overhead
    )
    
    # 텍스트가 한 번에 처리 가능한지 확인
    # 안전하게 80%만 사용하여 토큰 추정 오차를 고려
    safe_max_chars = int(max_chunk_chars * 0.8)
    if len(text) <= safe_max_chars:
        # 한 번에 처리 가능하면 기존 방식 사용
        logger.info("[LLM Summarizer] 텍스트가 짧아 한 번에 처리합니다.")
        return _summarize_chunk_with_lmstudio(text, 1, 1, settings)
    
    logger.info(
        "[LLM Summarizer] 텍스트가 길어 청크 분할 요약을 사용합니다. "
        "전체 길이: %d chars, 청크당 최대: %d chars (약 %d 토큰)",
        len(text), max_chunk_chars, available_tokens
    )
    
    # 텍스트를 더 작은 청크로 분할하여 컨텍스트 초과 방지
    # 안전하게 7000 토큰으로 제한 (프롬프트 오버헤드 고려)
    chunks = _split_text_into_chunks(text, max_tokens_per_chunk=7000, overlap_tokens=500)
    logger.info("텍스트를 %d개의 청크로 분할했습니다.", len(chunks))
    
    # 각 청크 요약
    successful_chunks = []
    failed_chunks = []
    for i, chunk in enumerate(chunks, 1):
        logger.info("청크 %d/%d 요약 중... (길이: %d chars)", i, len(chunks), len(chunk))
        try:
            chunk_summary = _summarize_chunk_with_lmstudio(chunk, i, len(chunks), settings)
            successful_chunks.append({
                "chunk_index": i,
                "summary": chunk_summary
            })
            logger.info("청크 %d/%d 요약 성공", i, len(chunks))
        except Exception as exc:
            logger.error("청크 %d 요약 실패: %s", i, exc)
            failed_chunks.append(i)
            # 실패한 청크는 건너뛰고 계속 진행 (에러 메시지는 포함하지 않음)
    
    if not successful_chunks:
        raise RuntimeError("모든 청크 요약이 실패했습니다.")
    
    # 실패한 청크가 있으면 경고 로그
    if failed_chunks:
        logger.warning(
            "일부 청크 요약 실패: 실패한 청크=%s, 성공한 청크=%d/%d",
            failed_chunks, len(successful_chunks), len(chunks)
        )
    
    # 통합 요약 생성 (성공한 청크만 사용)
    logger.info("청크 요약을 통합하여 최종 요약 생성 중... (성공한 청크: %d/%d)", len(successful_chunks), len(chunks))
    combined_summaries = "\n\n".join([
        f"## 부분 {cs['chunk_index']}\n\n{cs['summary']}"
        for cs in successful_chunks
    ])
    
    # 통합 요약 프롬프트 개선
    failed_parts_note = ""
    if failed_chunks:
        failed_parts_note = f"\n\nNote: Some parts ({', '.join(map(str, failed_chunks))}) failed to summarize and are excluded from this summary."
    
    merge_prompt = """당신은 회의록을 요약하는 전문가입니다. 다음은 긴 회의 전사의 여러 부분에 대한 요약입니다. 모든 부분을 통합하여 하나의 포괄적인 요약을 작성하고 적절한 제목을 생성하세요.

1. (필수) title: 통합된 요약에 대한 적절한 제목을 생성하세요. 제목만 보고도 회의의 대략적인 내용을 추측할 수 있어야 합니다. 제목은 반드시 한글로 작성하세요.

2. summary: 모든 부분을 통합하여 요약하세요.
   - 모든 내용은 반드시 한글로 작성하세요
   - `## 요약` 제목으로 시작하세요
   - 모든 부분의 핵심 내용을 불릿 포인트로 제공하세요
   - `## 세부 사항` 섹션에 결정 사항이나 액션 아이템을 번호로 나열하세요
   - 요약이 모든 부분의 중요한 내용을 다루도록 하세요
   - 중복 정보를 제거하고 유사한 내용을 통합하세요
   - 실제 회의 내용에만 집중하세요. 에러 메시지, 기술 정보, 플레이스홀더 텍스트는 무시하세요
   - 출력은 유효한 마크다운 형식이어야 합니다
   - 원본 요약이나 에러 메시지를 포함하지 마세요
   - 일부 부분이 누락된 경우, 사용 가능한 내용만 요약하세요{failed_parts_note}

---

다음 JSON 형식으로 반드시 응답하세요:

{{
    "title": "제목",  # 필수. 한글로 작성
    "summary": "## 요약\\n\\n- 내용...\\n\\n## 세부 사항\\n\\n1. 항목..."
}}

부분별 요약:
{transcript}
"""
    
    try:
        # 통합 요약에는 custom_prompt를 사용해야 하므로 별도 처리
        prompt = merge_prompt.format(transcript=combined_summaries, failed_parts_note=failed_parts_note)
        system_prompt = settings.lmstudio_system_prompt.strip()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        
        raw_response = request_chat_completion(settings=settings, messages=messages, stream=False)
        raw_response = raw_response.strip()
        if not raw_response:
            raise RuntimeError("LM Studio 통합 요약 결과가 비어 있습니다.")
        
        logger.info("청크 분할 요약 완료 (최종 응답 길이: %d chars)", len(raw_response))
        return raw_response
    except Exception as exc:
        # 통합 요약 실패 시 개별 요약을 합쳐서 반환
        logger.warning("통합 요약 실패, 개별 요약을 합쳐서 반환: %s", exc)
        return f"## 요약\n\n{combined_summaries}"


def _extract_text(completion: dict[str, Any]) -> str:
    """llama_cpp 응답에서 텍스트 추출."""
    choices = completion.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("text") or "")


def extract_title(summary_md: str, transcript_text: str) -> str:
    """
    요약 텍스트와 전사 텍스트를 기반으로 제목을 추출합니다.
    
    Args:
        summary_md: LLM이 생성한 요약 마크다운 텍스트
        transcript_text: 원본 전사 텍스트
    
    Returns:
        추출된 제목 (최대 512자)
    """
    settings = get_settings()
    
    # 제목 추출 프롬프트 (한글로만 응답하도록 명확히 지시)
    title_prompt = """다음 회의록 요약을 보고 적절한 제목을 한글로 하나만 제시해주세요.

중요:
- 반드시 한글로만 작성하세요
- 회의의 핵심 주제를 반영하는 간결한 제목
- 50자 이내로 작성
- 제목만 출력하고 다른 설명, 영어, 프롬프트 지시사항은 절대 포함하지 마세요
- 마크다운 형식이나 따옴표 없이 순수 한글 텍스트로만 출력

회의록 요약:
{summary}

제목:"""

    prompt = title_prompt.format(summary=summary_md[:2000])  # 요약의 앞부분만 사용
    
    try:
        if settings.llm_provider == "ollama":
            title = _extract_title_with_ollama(prompt, settings)
        elif settings.llm_provider == "llama_cpp":
            title = _extract_title_with_llama_cpp(prompt, settings)
        elif settings.llm_provider == "lmstudio":
            title = _extract_title_with_lmstudio(prompt, settings)
        else:
            logger.warning("지원하지 않는 LLM provider로 제목 추출 실패: %s", settings.llm_provider)
            return _extract_title_fallback(summary_md, transcript_text)
        
        # 제목 정리 (마크다운 제거, 따옴표 제거, 공백 정리)
        title = title.strip()
        
        # 프롬프트 텍스트가 포함된 경우 제거 시도
        # "제목:" 이후의 텍스트만 추출
        title_marker = "제목:"
        if title_marker in title:
            parts = title.split(title_marker, 1)
            if len(parts) > 1:
                title = parts[1].strip()
        
        # 영어 프롬프트 패턴이 시작 부분에 있으면 제거
        # "The user wants..." 같은 패턴 제거
        english_prompt_starters = [
            "the user wants",
            "user wants",
            "we need to",
            "they gave",
            "basically says",
        ]
        title_lower = title.lower()
        for starter in english_prompt_starters:
            if title_lower.startswith(starter):
                # 첫 문장 끝까지 제거 시도
                first_period = title.find(".")
                if first_period > 0:
                    title = title[first_period + 1:].strip()
                else:
                    # 마침표가 없으면 첫 줄 제거
                    first_newline = title.find("\n")
                    if first_newline > 0:
                        title = title[first_newline + 1:].strip()
                    else:
                        # 줄바꿈도 없으면 전체를 무효로 처리
                        return _extract_title_fallback(summary_md, transcript_text)
                break
        
        # 프롬프트 설명 부분 제거 (예: "The user wants..." 같은 부분)
        # 첫 번째 문장만 사용 (마침표나 줄바꿈 기준)
        first_period = title.find(".")
        first_newline = title.find("\n")
        if first_period > 0 and (first_newline < 0 or first_period < first_newline):
            # 마침표가 있고, 줄바꿈보다 먼저 나오면 마침표까지만 사용
            # 하지만 프롬프트 설명일 가능성이 있으므로 체크
            potential_title = title[:first_period].strip()
            if not _looks_like_invalid_title(potential_title) and len(potential_title) > 5:
                title = potential_title
        elif first_newline > 0:
            # 줄바꿈이 있으면 첫 줄만 사용
            title = title[:first_newline].strip()
        
        # 마크다운 헤더 제거 (# 제목)
        if title.startswith("#"):
            title = title.lstrip("#").strip()
        # 따옴표 제거
        title = title.strip('"\'')
        # 줄바꿈 제거
        title = title.replace("\n", " ").strip()
        # 최대 길이 제한
        if len(title) > 512:
            title = title[:509] + "..."
        
        # 한글이 포함되지 않은 제목은 무효로 처리
        if not _has_korean_characters(title):
            logger.warning("제목에 한글이 포함되지 않음, 대체 방법 사용: %s", title[:100])
            return _extract_title_fallback(summary_md, transcript_text)
        
        if not title or _looks_like_invalid_title(title):
            return _extract_title_fallback(summary_md, transcript_text)
        
        logger.info("제목 추출 완료: %s (길이: %d chars)", title, len(title))
        return title
        
    except Exception as exc:
        logger.warning("제목 추출 실패, 대체 방법 사용: %s", exc)
        return _extract_title_fallback(summary_md, transcript_text)


def _extract_title_with_ollama(prompt: str, settings) -> str:
    """Ollama를 사용한 제목 추출."""
    ollama_url = f"{settings.ollama_base_url}/api/generate"
    payload = {
        "model": settings.ollama_model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,  # 제목 추출은 더 낮은 temperature 사용
            "num_predict": 100,  # 제목은 짧으므로 토큰 수 제한
        },
    }
    
    with httpx.Client(timeout=30.0) as client:
        response = client.post(ollama_url, json=payload)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "").strip()


def _extract_title_with_llama_cpp(prompt: str, settings) -> str:
    """llama_cpp를 사용한 제목 추출."""
    from llama_cpp import Llama
    
    llm = Llama(
        model_path=settings.llama_cpp_model_path,
        n_ctx=512,  # 제목 추출은 짧은 컨텍스트로 충분
        n_threads=settings.llama_cpp_n_threads,
        verbose=False,
    )
    
    completion = llm(
        prompt,
        max_tokens=100,
        temperature=0.3,
        stop=["\n\n", "제목:", "Title:"],
    )
    
    return _extract_text(completion)


def _extract_title_with_lmstudio(prompt: str, settings) -> str:
    """LM Studio를 사용한 제목 추출."""
    from .lmstudio_client import request_chat_completion
    
    system_prompt = "당신은 회의록 요약에서 제목을 추출하는 도우미입니다. 한글로만 제목을 출력하세요. 다른 설명이나 영어는 절대 포함하지 마세요."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    
    title = request_chat_completion(
        settings=settings,
        messages=messages,
        temperature=0.3,
        max_tokens=100,
        stream=False,
    )
    
    # reasoning 필드가 포함되어 있을 수 있으므로 제거
    # reasoning은 보통 "The user:" 같은 패턴으로 시작
    title_lower = title.lower()
    if "reasoning" in title_lower or "the user:" in title_lower or title_lower.startswith("the user"):
        # 첫 번째 실제 제목 부분만 추출 시도
        # "제목:" 마커 이후의 텍스트 사용
        if "제목:" in title:
            parts = title.split("제목:", 1)
            if len(parts) > 1:
                title = parts[1].strip()
        # 영어 프롬프트가 시작 부분에 있으면 제거
        elif title_lower.startswith("the user") or title_lower.startswith("user wants"):
            first_period = title.find(".")
            if first_period > 0:
                title = title[first_period + 1:].strip()
            else:
                first_newline = title.find("\n")
                if first_newline > 0:
                    title = title[first_newline + 1:].strip()
    
    return title


def _extract_title_fallback(summary_md: str, transcript_text: str) -> str:
    """
    LLM 추출 실패 시 대체 방법으로 제목 생성.
    요약의 첫 번째 헤더나 전사 텍스트의 첫 문장을 사용.
    """
    # 요약의 첫 번째 헤더 추출 시도
    lines = summary_md.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            if title and len(title) <= 512:
                return title
        elif line.startswith("## "):
            title = line[3:].strip()
            if title and len(title) <= 512:
                return title
    
    # 전사 텍스트의 첫 문장 사용 (최대 100자)
    first_sentence = transcript_text.split(".")[0].strip()
    if first_sentence:
        if len(first_sentence) > 100:
            first_sentence = first_sentence[:97] + "..."
        return first_sentence
    
    # 모두 실패하면 기본값
    return "회의록"


PROMPT_ECHO_MARKERS = [
    "you are an expert meeting summarizer",
    "guidelines:",
    "the user wants a title",
    "제목:",
    "please create a unified, comprehensive summary",
    "always answer in rhymes",
    "오늘은 목요일",
    "today is thursday",
    "in rhyme i'll say",
    "the user:",
    "we have a long transcript",
    "the prompt says",
    "the instruction:",
    "we need to produce",
    "they gave a summary",
    "basically says nothing",
    "it's empty",
    "nothing was found",
    "summarize the following",
    "korean transcription",
]

TITLE_PROMPT_MARKERS = [
    "the user wants",
    "user wants",
    "wants a title",
    "wants a concise",
    "concise title",
    "reflecting the core",
    "reflecting core",
    "core subject",
    "50 characters",
    "characters or less",
    "no markdown",
    "no quotes",
    "제목:",
    "요구사항",
    "please create",
    "create a concise",
    "마크다운",
    "we need to produce",
    "they gave a summary",
    "basically says nothing",
    "reflecting core topic",
    "within 50 characters",
    "다음 회의록 요약을 보고",
    "적절한 제목을 하나만",
    "회의의 핵심 주제를 반영",
    "제목만 출력하고",
    "다른 설명은 포함하지",
    "반드시 한글로만",
    "중요:",
]

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[\.!?])\s+|\n+")


def sanitize_summary_output(summary_md: str, transcript_text: str) -> str:
    """
    LLM 요약 결과에서 프롬프트 잔여물 제거 및 필요시 대체 요약 생성.
    """
    if not summary_md:
        logger.warning("LLM summary was empty, falling back to heuristic summary.")
        return _build_fallback_summary(transcript_text)
    
    text = summary_md.strip()
    
    # reasoning 필드가 포함되어 있는지 확인 (JSON 응답에서 reasoning이 포함된 경우)
    # reasoning은 보통 프롬프트 지시사항을 포함하므로 제거
    text_lower = text.lower()
    if "reasoning" in text_lower and ("the user:" in text_lower or "we need to" in text_lower):
        # reasoning 부분을 찾아서 제거 시도
        reasoning_start = text_lower.find("reasoning")
        if reasoning_start > 0:
            # reasoning 이전 부분만 사용
            text = text[:reasoning_start].strip()
            logger.info("Removed reasoning section from summary output")
    
    lower = text.lower()
    heading_idx = lower.find("## 요약")
    if heading_idx > 0:
        # "## 요약" 이전의 텍스트가 프롬프트일 가능성이 높으므로 제거
        text = text[heading_idx:]
    
    # 프롬프트 에코 체크
    if _looks_like_prompt_echo(text):
        logger.warning("Detected prompt instructions in summary, generating heuristic summary instead.")
        return _build_fallback_summary(transcript_text)
    
    # 프롬프트 패턴이 포함된 줄 제거
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line_lower = line.lower().strip()
        # 프롬프트 마커가 포함된 줄은 제거
        if any(marker in line_lower for marker in PROMPT_ECHO_MARKERS):
            logger.debug("Removing prompt-like line from summary: %s", line[:100])
            continue
        # "Today is Thursday" 같은 시스템 프롬프트 제거
        if "today is thursday" in line_lower or "in rhyme" in line_lower:
            continue
        cleaned_lines.append(line)
    
    text = "\n".join(cleaned_lines).strip()
    
    # 정제 후에도 여전히 프롬프트처럼 보이면 대체 요약 사용
    if _looks_like_prompt_echo(text):
        logger.warning("Summary still contains prompt instructions after cleaning, using fallback.")
        return _build_fallback_summary(transcript_text)
    
    # 요약에 한글이 포함되어 있는지 확인 (최소한 일부 한글이 있어야 함)
    # 완전히 영어로만 된 요약은 프롬프트일 가능성이 높음
    if not _has_korean_characters(text):
        # 한글이 전혀 없으면 대체 요약 사용
        logger.warning("Summary contains no Korean characters, using fallback.")
        return _build_fallback_summary(transcript_text)
    
    return text


def _looks_like_prompt_echo(text: str) -> bool:
    cleaned = text.strip().lower()
    if not cleaned:
        return True
    if len(cleaned) < 40:
        return True
    return any(marker in cleaned for marker in PROMPT_ECHO_MARKERS)


def _has_korean_characters(text: str) -> bool:
    """텍스트에 한글이 포함되어 있는지 확인"""
    import re
    return bool(re.search(r'[가-힣]', text))


def _looks_like_invalid_title(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return True
    
    cleaned_lower = cleaned.lower()
    
    # 프롬프트 마커가 포함되어 있으면 무효
    if any(marker in cleaned_lower for marker in TITLE_PROMPT_MARKERS):
        return True
    
    # 영어로만 작성된 긴 텍스트는 프롬프트일 가능성이 높음
    # 한글이 없고 영어 단어가 여러 개 포함되어 있으면 프롬프트로 간주
    if not _has_korean_characters(cleaned):
        # 영어 단어가 5개 이상이면 프롬프트로 간주
        import re
        english_words = re.findall(r'\b[a-z]+\b', cleaned_lower)
        if len(english_words) >= 5:
            return True
        # 특정 프롬프트 패턴이 포함되어 있으면 무효
        prompt_patterns = [
            "the user",
            "user wants",
            "wants a",
            "concise title",
            "reflecting",
            "core subject",
            "characters",
            "or less",
            "no markdown",
            "no quotes",
        ]
        if any(pattern in cleaned_lower for pattern in prompt_patterns):
            return True
    
    # 프롬프트처럼 보이는 긴 설명문이면 무효 (50자 이상이고 특정 패턴 포함)
    if len(cleaned) > 50:
        prompt_patterns = [
            "we need to",
            "they gave",
            "basically says",
            "it's empty",
            "nothing was found",
            "produce a concise",
            "reflecting core topic",
        ]
        if any(pattern in cleaned_lower for pattern in prompt_patterns):
            return True
    
    return False


def _extract_sentences(transcript_text: str) -> list[str]:
    if not transcript_text:
        return []
    normalized = transcript_text.replace("\r", " ")
    raw_sentences = SENTENCE_SPLIT_REGEX.split(normalized)
    sentences: list[str] = []
    for raw in raw_sentences:
        fragment = raw.strip()
        if not fragment:
            continue
        if _looks_like_prompt_echo(fragment):
            continue
        sentences.append(fragment)
    return sentences


def _truncate_sentence(sentence: str, max_len: int = 120) -> str:
    sentence = sentence.strip()
    if len(sentence) <= max_len:
        return sentence
    return sentence[: max_len - 3].rstrip() + "..."


def _build_fallback_summary(transcript_text: str) -> str:
    sentences = _extract_sentences(transcript_text)
    summary_items = sentences[:3]
    if not summary_items:
        summary_items = ["전사 텍스트에서 핵심 내용을 충분히 찾지 못했습니다."]
    detail_items = sentences[3:6]
    if not detail_items:
        detail_items = ["전사 텍스트에서 명확한 결정 사항이나 액션 아이템을 확인하지 못했습니다."]
    
    summary_section = "\n".join(f"- {_truncate_sentence(item)}" for item in summary_items)
    detail_section = "\n".join(
        f"{idx}. {_truncate_sentence(item)}"
        for idx, item in enumerate(detail_items, 1)
    )
    
    return f"""## 요약
{summary_section}

## 세부 사항
{detail_section}"""


