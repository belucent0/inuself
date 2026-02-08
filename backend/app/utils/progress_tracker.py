"""다단계 작업 진행률 추적기

여러 단계(phase)로 구성된 작업의 진행률을 하나의 연속된 0-100% 구간으로
매핑하고, throttling 및 monotonic 보정을 적용하여 콜백으로 발행합니다.

Usage:
    tracker = ProgressTracker(
        phases=[Phase("video", 5, 55), Phase("audio", 55, 95)],
        on_progress=lambda p, step, msg: publish(p, step, msg),
    )
    tracker.set(3.0, "메타데이터 추출 중...", step="metadata")
    tracker.update(50.0, "다운로드 중...")   # → 30.0%
    tracker.advance_phase()
    tracker.update(50.0, "다운로드 중...")   # → 75.0%
    tracker.set(95.0, "업로드 중...", step="upload")
"""

from __future__ import annotations

import time
from typing import Callable, NamedTuple

from .event_publisher import publish_file_progress


class Phase(NamedTuple):
    """진행률 매핑 단계"""

    name: str
    start: float  # 전체 진행률 시작 (0-100)
    end: float  # 전체 진행률 끝 (0-100)


class ProgressTracker:
    """다단계 작업 진행률 추적기

    특성:
    - 다단계 매핑: 각 단계의 raw 0-100%를 전체 구간에 매핑
    - Monotonic: 진행률이 절대 감소하지 않음
    - Throttling: 지정 간격/변화량 이상일 때만 발행

    Args:
        phases: 단계 목록. 각 Phase(name, start, end)
        on_progress: 발행 콜백 (progress, step_name, message)
        throttle_sec: 최소 발행 간격 (초)
        min_delta: 최소 변화량 (%) — 이 이상 변해야 발행
    """

    def __init__(
        self,
        phases: list[Phase],
        on_progress: Callable[[float, str, str], None],
        throttle_sec: float = 1.0,
        min_delta: float = 3.0,
    ):
        self._phases = phases
        self._on_progress = on_progress
        self._throttle_sec = throttle_sec
        self._min_delta = min_delta
        self._phase_idx = 0
        self._published = 0.0
        self._last_time = 0.0
        self._raw = 0.0

    @property
    def progress(self) -> float:
        """현재 발행된 전체 진행률"""
        return self._published

    @property
    def current_phase(self) -> Phase | None:
        """현재 활성 단계"""
        if self._phase_idx < len(self._phases):
            return self._phases[self._phase_idx]
        return self._phases[-1] if self._phases else None

    def set(self, progress: float, message: str = "", step: str = ""):
        """직접 진행률 설정 (throttling 무시, 즉시 발행)

        메타데이터 추출, S3 업로드 시작 등 단계 전환 지점에서 사용.
        """
        progress = max(progress, self._published)
        self._published = progress
        self._last_time = time.time()
        phase = self.current_phase
        self._on_progress(
            round(progress, 1),
            step or (phase.name if phase else ""),
            message,
        )

    def advance_phase(self):
        """현재 단계 완료 → 다음 단계로 이동

        현재 단계의 end 값까지 published를 올리고,
        다음 단계의 raw를 0으로 초기화합니다.
        """
        phase = self.current_phase
        if phase:
            self._published = max(self._published, phase.end)
        self._phase_idx += 1
        self._raw = 0.0

    def update(self, raw: float, message: str = ""):
        """현재 단계 내 raw progress (0-100) 업데이트

        Args:
            raw: 현재 단계 내 진행률 (0-100)
            message: 표시 메시지
        """
        # Monotonic within phase
        raw = max(raw, self._raw)
        self._raw = raw

        phase = self.current_phase
        if not phase:
            return

        # Map to overall progress
        mapped = phase.start + (raw / 100.0) * (phase.end - phase.start)
        mapped = max(mapped, self._published)

        # Throttle
        now = time.time()
        should_publish = (
            raw >= 100.0
            or (now - self._last_time >= self._throttle_sec)
            or (mapped - self._published >= self._min_delta)
        )

        if should_publish:
            self._published = mapped
            self._last_time = now
            self._on_progress(round(mapped, 1), phase.name, message)


class PipelineProgress:
    """처리 파이프라인 상태 전환 시 SSE 이벤트 발행기.

    ASR/OCR/LLM 등 이벤트 기반 파이프라인에서 상태 전환마다
    일관된 progress 이벤트를 발행합니다.

    - 중간 단계(started)는 progress=0 → 클라이언트 estimation이 동작
    - 완료 단계는 progress=100
    - 실패 단계는 progress=0

    Usage:
        progress = PipelineProgress(file_id)
        progress.asr_started()
        # ... ASR 처리 ...
        progress.asr_completed(duration_seconds=120, speakers=["A", "B"])
        progress.llm_started()
        # ... LLM 처리 ...
        progress.llm_completed(title="제목")
    """

    def __init__(self, file_id: str):
        self._file_id = file_id

    def _emit(
        self,
        status: str,
        step: str,
        progress: float,
        message: str,
        metadata: dict | None = None,
    ):
        publish_file_progress(
            file_id=self._file_id,
            status=status,
            step=step,
            progress=progress,
            message=message,
            metadata=metadata,
        )

    # ── ASR ──────────────────────────────────────────────
    def asr_started(self):
        self._emit("PROCESSING", "asr", 0, "음성 인식 처리 중...")

    def asr_completed(self, **metadata):
        self._emit("SUMMARY_QUEUED", "asr_completed", 0,
                    "음성 인식 완료, 요약 대기 중...", metadata or None)

    def asr_failed(self, error: str):
        self._emit("ASR_FAILED", "asr_failed", 0, f"음성 인식 실패: {error}")

    # ── OCR ──────────────────────────────────────────────
    def ocr_started(self):
        self._emit("OCR_PROCESSING", "ocr", 0, "문서 인식 처리 중...")

    def ocr_completed(self, **metadata):
        self._emit("SUMMARY_QUEUED", "ocr_completed", 0,
                    "문서 인식 완료, 요약 대기 중...", metadata or None)

    def ocr_failed(self, error: str):
        self._emit("OCR_FAILED", "ocr_failed", 0, f"문서 인식 실패: {error}")

    # ── LLM ──────────────────────────────────────────────
    def llm_progress(self, progress: float, message: str):
        """LLM 요약 중간 진행률"""
        self._emit("SUMMARIZING", "llm", progress, message)

    def llm_started(self):
        self._emit("SUMMARIZING", "llm", 0, "요약 생성 중...")

    def llm_completed(self, **metadata):
        self._emit("COMPLETED", "completed", 100,
                    "모든 처리가 완료되었습니다.", metadata or None)

    def llm_failed(self, error: str):
        self._emit("SUMMARY_FAILED", "llm_failed", 0, f"요약 생성 실패: {error}")

    # ── 공통 ─────────────────────────────────────────────
    def completed_empty(self, message: str = "처리 완료"):
        self._emit("COMPLETED", "completed", 100, message)
