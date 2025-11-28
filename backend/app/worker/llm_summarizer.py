from __future__ import annotations

import logging
from typing import Any

import httpx

from ..core.config import get_settings
from .lmstudio_client import LMStudioClientError, request_chat_completion

logger = logging.getLogger(__name__)

DEFAULT_SUMMARY_PROMPT = """You are an expert meeting summarizer.
Summarize the following Korean transcription into concise Markdown.

Guidelines:
- Start with a level-2 heading `## 요약`.
- Provide bullet points for 핵심 내용.
- Add another section `## 세부 사항` with numbered items for decisions or action items.
- Preserve speaker names if provided.
- The output must be valid Markdown. Do not include the raw transcript.

Transcript:
{transcript}
"""


def summarize_transcription(text: str) -> str:
    """Ollama API를 사용해 전사 텍스트를 Markdown 요약으로 변환한다."""
    normalized = text.strip()
    if not normalized:
        raise ValueError("요약할 전사 텍스트가 비어 있습니다.")

    settings = get_settings()
    
    # Ollama 사용
    if settings.llm_provider == "ollama":
        return _summarize_with_ollama(normalized, settings)
    # llama_cpp 직접 사용 (기존 방식)
    elif settings.llm_provider == "llama_cpp":
        return _summarize_with_llama_cpp(normalized, settings)
    elif settings.llm_provider == "lmstudio":
        return _summarize_with_lmstudio(normalized, settings)
    else:
        raise ValueError(f"지원하지 않는 LLM provider: {settings.llm_provider}")


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
            
            summary = result.get("response", "").strip()
            if not summary:
                raise RuntimeError("LLM 요약 결과가 비어 있습니다.")
            if not summary.startswith("##"):
                summary = f"## 요약\n\n{summary}"
            
            logger.info("Ollama 요약 완료 (길이: %d chars)", len(summary))
            return summary
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
    
    summary = _extract_text(response).strip()
    if not summary:
        raise RuntimeError("LLM 요약 결과가 비어 있습니다.")
    if not summary.startswith("##"):
        summary = f"## 요약\n\n{summary}"
    return summary


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
        summary = request_chat_completion(settings=settings, messages=messages, stream=False)
        summary = summary.strip()
        if not summary:
            raise RuntimeError("LM Studio 요약 결과가 비어 있습니다.")
        if not summary.startswith("##"):
            summary = f"## 요약\n\n{summary}"
        logger.info("청크 %d/%d 요약 완료 (길이: %d chars)", chunk_index, total_chunks, len(summary))
        return summary
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
    
    merge_prompt = """You are an expert meeting summarizer.
The following are summaries of different parts of a long meeting transcription.
Please create a unified, comprehensive summary that combines all parts.

Guidelines:
- Start with a level-2 heading `## 요약`.
- Provide bullet points for 핵심 내용 from all parts.
- Add another section `## 세부 사항` with numbered items for decisions or action items.
- Ensure the summary covers all important points from all parts.
- Remove redundant information and merge similar points.
- Focus only on the actual meeting content. Ignore any error messages, technical information, or placeholder text.
- The output must be valid Markdown. Do not include the raw summaries or any error messages.
- If some parts are missing, summarize only what is available.{failed_parts_note}

Part Summaries:
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
        
        final_summary = request_chat_completion(settings=settings, messages=messages, stream=False)
        final_summary = final_summary.strip()
        if not final_summary:
            raise RuntimeError("LM Studio 통합 요약 결과가 비어 있습니다.")
        if not final_summary.startswith("##"):
            final_summary = f"## 요약\n\n{final_summary}"
        
        logger.info("청크 분할 요약 완료 (최종 요약 길이: %d chars)", len(final_summary))
        return final_summary
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


