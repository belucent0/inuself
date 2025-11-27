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


def _summarize_with_lmstudio(text: str, settings) -> str:
    """LM Studio Chat Completions API를 통한 요약."""
    prompt = DEFAULT_SUMMARY_PROMPT.format(transcript=text)
    system_prompt = settings.lmstudio_system_prompt.strip()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    try:
        summary = request_chat_completion(settings=settings, messages=messages, stream=False)
    except LMStudioClientError as exc:
        error_msg = f"LM Studio 요약 실패: {exc}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from exc

    summary = summary.strip()
    if not summary:
        raise RuntimeError("LM Studio 요약 결과가 비어 있습니다.")
    if not summary.startswith("##"):
        summary = f"## 요약\n\n{summary}"
    logger.info("LM Studio 요약 완료 (길이: %d chars)", len(summary))
    return summary


def _extract_text(completion: dict[str, Any]) -> str:
    """llama_cpp 응답에서 텍스트 추출."""
    choices = completion.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("text") or "")


