"""WPI AI 리포트 처리 프로세서."""

import asyncio
import json
import re
from functools import partial
from typing import Any
from uuid import uuid4

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.event_loop import cleanup_worker_event_loop, setup_worker_event_loop
from worker.utils.result_publisher import (
    publish_wpi_report_completed,
    publish_wpi_report_failed,
    publish_wpi_report_started,
)
from worker.utils.storage import upload_json

settings = get_settings()


def _extract_context_from_messages(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """백엔드 user prompt에서 context_json을 추출한다."""

    for message in messages:
        if message.get("role") != "user":
            continue

        content = str(message.get("content") or "")
        if not content:
            continue

        block_match = re.search(
            r"## 입력 데이터\s*(.*?)\s*## 작성 형식",
            content,
            re.DOTALL,
        )
        if block_match:
            raw_json = block_match.group(1).strip()
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(content[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

    return None


def process_wpi_report_job(
    *,
    scan_result_id: str,
    messages: list[dict[str, Any]],
) -> None:
    """WPI AI 리포트 생성 작업 진입점."""

    logger.info("[WPI Report] Job started: scan_result_id=%s", scan_result_id)
    loop = setup_worker_event_loop()

    try:
        loop.run_until_complete(
            _process_job_async(scan_result_id=scan_result_id, messages=messages)
        )
        logger.info("[WPI Report] Job completed: scan_result_id=%s", scan_result_id)
    except Exception as exc:
        logger.error(
            "[WPI Report] Job failed: scan_result_id=%s, error=%s",
            scan_result_id,
            exc,
        )
        raise
    finally:
        cleanup_worker_event_loop(loop)


async def _process_job_async(
    *,
    scan_result_id: str,
    messages: list[dict[str, Any]],
) -> None:
    if not messages:
        raise ValueError("WPI report messages are empty")

    from worker.processors.wpi_report_graph_executor import WpiReportGraphExecutor
    from worker.pipelines.llm.litellm_client import request_litellm_completion

    try:
        publish_wpi_report_started(scan_result_id)

        context = _extract_context_from_messages(messages)
        generation_mode = "single_prompt"
        response = ""

        if context:
            try:
                graph_executor = WpiReportGraphExecutor(settings=settings)
                response = await asyncio.wait_for(
                    graph_executor.execute(context),
                    timeout=settings.wpi_report_graph_timeout_seconds,
                )
                generation_mode = "langgraph"
                logger.info(
                    "[WPI Report] LangGraph generation completed: scan_result_id=%s",
                    scan_result_id,
                )
            except Exception as graph_exc:
                logger.warning(
                    "[WPI Report] LangGraph generation failed, fallback to single prompt: "
                    "scan_result_id=%s, error=%s",
                    scan_result_id,
                    graph_exc,
                )

        if not response:
            llm_call = partial(
                request_litellm_completion,
                settings=settings,
                messages=messages,
                model=settings.litellm_model_summarize,
                request_timeout_seconds=settings.wpi_report_llm_request_timeout_seconds,
                max_retry_time=settings.wpi_report_llm_busy_max_seconds,
                retry_interval=settings.wpi_report_llm_retry_interval_seconds,
            )
            response = await asyncio.wait_for(
                asyncio.to_thread(llm_call),
                timeout=settings.wpi_report_single_prompt_timeout_seconds,
            )
            logger.info(
                "[WPI Report] Single prompt generation completed: scan_result_id=%s",
                scan_result_id,
            )
    except Exception as exc:
        publish_wpi_report_failed(scan_result_id, error=str(exc))
        raise

    result_data = {
        "scan_result_id": scan_result_id,
        "raw_response": response,
        "generation_mode": generation_mode,
    }
    result_s3_key = f"results/wpi_report/{scan_result_id}/{uuid4().hex}.json"
    upload_json(result_data, key=result_s3_key)

    publish_wpi_report_completed(scan_result_id, result_s3_key=result_s3_key)
