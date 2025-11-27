from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class SummarizerConfig:
    model_path: Path
    context_length: int
    max_tokens: int
    temperature: float
    top_p: float
    n_threads: int
    use_vulkan: bool


def summarize_transcript_to_markdown(
    transcript: str,
    *,
    config: SummarizerConfig,
    prompt_template: str = DEFAULT_SUMMARY_PROMPT,
) -> str:
    """llama.cpp를 사용해 전사 텍스트를 Markdown으로 요약."""
    normalized = transcript.strip()
    if not normalized:
        raise ValueError("요약할 전사 텍스트가 비어 있습니다.")

    prompt = prompt_template.format(transcript=normalized)
    llama = _get_model(
        model_path=str(config.model_path),
        context_length=config.context_length,
        n_threads=config.n_threads,
        use_vulkan=config.use_vulkan,
    )
    response = llama.create_completion(
        prompt=prompt,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        top_p=config.top_p,
        stream=False,
        echo=False,
    )

    summary = _extract_text(response).strip()
    if not summary:
        raise RuntimeError("LLM 요약 결과가 비어 있습니다.")

    if not summary.startswith("##"):
        summary = f"## 요약\n\n{summary}"
    return summary


def _extract_text(completion: dict[str, Any]) -> str:
    choices = completion.get("choices") or []
    if not choices:
        return ""
    text = choices[0].get("text") or ""
    return str(text)


@lru_cache(maxsize=2)
def _get_model(
    *,
    model_path: str,
    context_length: int,
    n_threads: int,
    use_vulkan: bool,
):
    from llama_cpp import Llama  # lazy import to avoid hard dependency at import time

    return Llama(
        model_path=model_path,
        n_ctx=context_length,
        n_threads=n_threads,
        logits_all=False,
        embedding=False,
        use_mlock=True,
        n_gpu_layers=-1 if use_vulkan else 0,
    )

