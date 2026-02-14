"""WPI 심리검사 서비스.

채점 로직, GAP 분석, 프로필 관리 등 비즈니스 로직 담당.
ScanResult 범용 테이블 사용.
"""

from __future__ import annotations

import json
import random
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import logger
from ..core.telemetry import preserve_otel_context
from ..db.models import ScanResult
from ..prompts.wpi_report import WPI_REPORT_SYSTEM_PROMPT, WPI_REPORT_USER_TEMPLATE
from ..repositories.scan_repository import ScanRepository
from ..schemas.wpi import (
    GAP_AXIS_MAP,
    I_TEST_TYPES,
    ME_TEST_TYPES,
    WpiQuestion,
    WpiResponses,
)
from ..utils.task_queue_adapter import get_task_queue
from .wpi_profile_parser import get_auto_report


# WPI 데이터 스키마 버전
# v1: 초기 구조 (2026-02-09)
WPI_DATA_VERSION = 1


class WpiService:
    """WPI 검사 채점 및 프로필 관리 서비스."""

    SCAN_TYPE = "wpi"

    # 문항 JSON 파일 경로
    QUESTION_DATA_DIR = Path(__file__).parent.parent / "data" / "wpi"
    I_TEST_FILE = QUESTION_DATA_DIR / "i-test-question.json"
    ME_TEST_FILE = QUESTION_DATA_DIR / "me-test-question.json"

    # 순위별 가중치 (1순위*7, 2순위*5, 3순위*3)
    RANK_WEIGHTS = {1: 7, 2: 5, 3: 3}
    AI_REPORT_STATUS_VALUES = {"idle", "queued", "processing", "completed", "failed"}
    AI_REPORT_PENDING_STATUSES = {"queued", "processing"}
    AI_REPORT_STALE_SECONDS = 15 * 60

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ScanRepository(session)
        self._i_test_data: list[dict[str, Any]] | None = None
        self._me_test_data: list[dict[str, Any]] | None = None

    def _load_questions(
        self, test_type: Literal["i_test", "me_test"]
    ) -> list[dict[str, Any]]:
        """JSON에서 문항 데이터 로드 (캐싱)."""
        if test_type == "i_test":
            if self._i_test_data is None:
                with open(self.I_TEST_FILE, encoding="utf-8") as f:
                    self._i_test_data = json.load(f)
            i_data = self._i_test_data
            if i_data is None:
                raise RuntimeError("Failed to load i-test questions")
            return i_data
        else:
            if self._me_test_data is None:
                with open(self.ME_TEST_FILE, encoding="utf-8") as f:
                    self._me_test_data = json.load(f)
            me_data = self._me_test_data
            if me_data is None:
                raise RuntimeError("Failed to load me-test questions")
            return me_data

    def get_questions(
        self, test_type: Literal["i_test", "me_test"], shuffle: bool = True
    ) -> list[WpiQuestion]:
        """문항 목록 반환."""
        questions = self._load_questions(test_type)
        result = [WpiQuestion(id=q["id"], text=q["text"]) for q in questions]
        if shuffle:
            random.shuffle(result)
        return result

    def calculate_scores(
        self, test_type: Literal["i_test", "me_test"], responses: WpiResponses
    ) -> dict[str, float]:
        """이중 가중치(순위 × 문항) 적용하여 유형별 절대 점수 산출."""
        questions = self._load_questions(test_type)
        item_map = {q["id"]: q for q in questions}

        type_scores: dict[str, float] = defaultdict(float)

        for rank, item_ids in [
            (1, responses.rank_1),
            (2, responses.rank_2),
            (3, responses.rank_3),
        ]:
            rank_weight = self.RANK_WEIGHTS[rank]
            for item_id in item_ids:
                item = item_map[item_id]
                score = rank_weight * item["weight"]
                type_scores[item["type"]] += score

        all_types = I_TEST_TYPES if test_type == "i_test" else ME_TEST_TYPES
        for t in all_types:
            if t not in type_scores:
                type_scores[t] = 0.0

        return dict(type_scores)

    def get_dominant_type(self, scores: dict[str, float]) -> str:
        """우세 유형 반환."""
        return max(scores, key=lambda k: scores[k])

    def calculate_gap_analysis(
        self, i_scores: dict[str, float], me_scores: dict[str, float]
    ) -> dict[str, dict[str, float | str]]:
        """I-Me 교차 축 GAP 분석."""
        axis_gaps = {}

        for axis_name, (i_type, me_type) in GAP_AXIS_MAP.items():
            i_score = i_scores.get(i_type, 0.0)
            me_score = me_scores.get(me_type, 0.0)
            gap = i_score - me_score

            axis_gaps[axis_name] = {
                "i_type": i_type,
                "me_type": me_type,
                "i_score": i_score,
                "me_score": me_score,
                "gap": gap,
            }

        return {"axis_gaps": axis_gaps}

    # === 프로필 관리 ===

    def enrich_with_scores(self, data: dict[str, Any]) -> dict[str, Any]:
        """raw_responses에서 점수를 동적으로 계산하여 데이터에 추가."""
        enriched = data.copy()

        try:
            # I-Test 점수 계산
            if enriched.get("i_test") and enriched["i_test"].get("raw_responses"):
                raw = enriched["i_test"]["raw_responses"]
                responses = WpiResponses(**raw)
                scores = self.calculate_scores("i_test", responses)
                dominant = self.get_dominant_type(scores)
                enriched["i_test"]["scores"] = scores
                enriched["i_test"]["dominant_type"] = dominant

            # Me-Test 점수 계산
            if enriched.get("me_test") and enriched["me_test"].get("raw_responses"):
                raw = enriched["me_test"]["raw_responses"]
                responses = WpiResponses(**raw)
                scores = self.calculate_scores("me_test", responses)
                dominant = self.get_dominant_type(scores)
                enriched["me_test"]["scores"] = scores
                enriched["me_test"]["dominant_type"] = dominant

            # GAP 분석 계산
            if (
                enriched.get("i_test")
                and enriched["i_test"].get("scores")
                and enriched.get("me_test")
                and enriched["me_test"].get("scores")
            ):
                enriched["gap_analysis"] = self.calculate_gap_analysis(
                    enriched["i_test"]["scores"],
                    enriched["me_test"]["scores"],
                )
        except FileNotFoundError as exc:
            logger.warning(
                "WPI question file missing; returning raw scan data without score enrichment: %s",
                exc,
            )
            return enriched

        return enriched

    def _parse_ai_report_updated_at(self, value: str | None) -> datetime | None:
        if not value:
            return None

        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _serialize_auto_profile(self, auto_report: Any) -> dict[str, Any] | None:
        if auto_report is None:
            return None

        return {
            "i_type_kr": auto_report.i_type_kr,
            "me_type_kr": auto_report.me_type_kr,
            "basic_need": auto_report.basic_need,
            "strengths": auto_report.strengths,
            "weaknesses": auto_report.weaknesses,
            "personality_description": auto_report.personality_description,
            "me_context_analysis": auto_report.me_context_analysis,
        }

    def _sort_scores(
        self,
        scores: dict[str, float],
        top_k: int = 3,
    ) -> list[dict[str, float | str]]:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [
            {"type": str(type_name), "score": float(score)}
            for type_name, score in ranked[:top_k]
        ]

    def _extract_gap_highlights(
        self,
        gap_analysis: dict[str, Any],
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        axis_gaps = (
            gap_analysis.get("axis_gaps") if isinstance(gap_analysis, dict) else None
        )
        if not isinstance(axis_gaps, dict):
            return []

        highlights: list[dict[str, Any]] = []
        for axis_name, payload in axis_gaps.items():
            if not isinstance(payload, dict):
                continue

            gap_value = float(payload.get("gap", 0.0))
            direction = "balanced"
            if gap_value > 0:
                direction = "i_test_dominant"
            elif gap_value < 0:
                direction = "me_test_dominant"

            highlights.append(
                {
                    "axis": str(axis_name),
                    "i_type": payload.get("i_type"),
                    "me_type": payload.get("me_type"),
                    "gap": gap_value,
                    "abs_gap": abs(gap_value),
                    "direction": direction,
                }
            )

        highlights.sort(key=lambda item: item["abs_gap"], reverse=True)
        return highlights[:top_k]

    def _read_ai_report_state(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("ai_report") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return {
                "status": "idle",
                "report_md": None,
                "error": None,
                "job_id": None,
                "updated_at": None,
            }

        status = str(payload.get("status", "idle")).lower()
        if status not in self.AI_REPORT_STATUS_VALUES:
            status = "idle"

        return {
            "status": status,
            "report_md": payload.get("report_md"),
            "error": payload.get("error"),
            "job_id": payload.get("job_id"),
            "updated_at": self._parse_ai_report_updated_at(payload.get("updated_at")),
        }

    def _is_ai_report_stale(self, updated_at: datetime | None) -> bool:
        if updated_at is None:
            return False

        now = datetime.now(timezone.utc)
        return (now - updated_at).total_seconds() >= self.AI_REPORT_STALE_SECONDS

    async def _get_celery_job_status(self, job_id: str | None) -> str | None:
        if not job_id:
            return None

        task_queue = get_task_queue()
        loop = asyncio.get_running_loop()
        get_status_func = partial(task_queue.get_job_status, job_id)

        try:
            status = await loop.run_in_executor(
                None,
                preserve_otel_context(get_status_func),
            )
            return str(status).upper()
        except Exception as exc:
            logger.warning(
                "WPI AI report job status check failed: result_job_id=%s, error=%s",
                job_id,
                exc,
            )
            return None

    async def _clear_wpi_active_job(self, result_id: UUID) -> None:
        task_queue = get_task_queue()
        loop = asyncio.get_running_loop()
        clear_func = partial(task_queue.clear_active_job, "wpi_report", str(result_id))
        await loop.run_in_executor(None, preserve_otel_context(clear_func))

    async def _write_ai_report_state(
        self,
        result: ScanResult,
        *,
        status: str,
        report_md: str | None,
        error: str | None,
        job_id: str | None,
    ) -> dict[str, Any]:
        payload = result.data.copy()
        payload["ai_report"] = {
            "status": status,
            "report_md": report_md,
            "error": error,
            "job_id": job_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.repo.update(result, data=payload)
        await self.session.commit()
        return self._read_ai_report_state(payload)

    async def _reconcile_ai_report_state(self, result: ScanResult) -> dict[str, Any]:
        state = self._read_ai_report_state(result.data)
        status = str(state.get("status"))
        if status not in self.AI_REPORT_PENDING_STATUSES:
            return state

        raw_job_id = state.get("job_id")
        job_id = str(raw_job_id) if raw_job_id else None
        celery_status = await self._get_celery_job_status(job_id)
        stale = self._is_ai_report_stale(state.get("updated_at"))

        failure_reason: str | None = None
        if celery_status in {"FAILURE", "REVOKED"}:
            failure_reason = f"AI 리포트 작업이 종료 상태({celery_status})로 확인되어 실패 처리되었습니다"
        elif celery_status == "SUCCESS":
            failure_reason = (
                "AI 리포트 작업은 종료되었지만 완료 이벤트를 수신하지 못했습니다. "
                "다시 생성을 시도해 주세요"
            )
        elif stale and (
            not job_id
            or celery_status in {None, "PENDING", "STARTED", "RETRY", "RECEIVED"}
        ):
            failure_reason = (
                "AI 리포트 작업이 일정 시간 이상 진행되지 않아 자동으로 실패 처리되었습니다. "
                "다시 생성을 시도해 주세요"
            )

        if not failure_reason:
            return state

        if job_id:
            await self._clear_wpi_active_job(result.id)

        logger.warning(
            "WPI AI report reconciled to failed: result_id=%s, previous_status=%s, celery_status=%s, stale=%s",
            result.id,
            status,
            celery_status,
            stale,
        )

        return await self._write_ai_report_state(
            result,
            status="failed",
            report_md=state.get("report_md"),
            error=failure_reason,
            job_id=job_id,
        )

    def _build_ai_report_messages(
        self, enriched_data: dict[str, Any]
    ) -> list[dict[str, str]]:
        i_test = enriched_data.get("i_test") or {}
        me_test = enriched_data.get("me_test") or {}

        i_type = i_test.get("dominant_type")
        me_type = me_test.get("dominant_type")
        if not i_type or not me_type:
            raise ValueError(
                "WPI 리포트를 생성하려면 i/me 테스트가 모두 완료되어야 합니다"
            )

        raw_i_scores_obj = i_test.get("scores")
        raw_me_scores_obj = me_test.get("scores")
        if not isinstance(raw_i_scores_obj, dict):
            raw_i_scores_obj = {}
        if not isinstance(raw_me_scores_obj, dict):
            raw_me_scores_obj = {}

        i_scores = {str(key): float(value) for key, value in raw_i_scores_obj.items()}
        me_scores = {str(key): float(value) for key, value in raw_me_scores_obj.items()}

        i_top_types = self._sort_scores(i_scores)
        me_top_types = self._sort_scores(me_scores)
        secondary_i_type = str(i_top_types[1]["type"]) if len(i_top_types) > 1 else None
        secondary_me_type = (
            str(me_top_types[1]["type"]) if len(me_top_types) > 1 else None
        )

        dominant_i_margin = None
        if len(i_top_types) >= 2:
            dominant_i_margin = float(i_top_types[0]["score"]) - float(
                i_top_types[1]["score"]
            )

        dominant_me_margin = None
        if len(me_top_types) >= 2:
            dominant_me_margin = float(me_top_types[0]["score"]) - float(
                me_top_types[1]["score"]
            )

        primary_auto_report = get_auto_report(str(i_type), str(me_type))

        secondary_profiles: list[dict[str, Any]] = []
        candidate_pairs: list[tuple[str | None, str | None]] = [
            (str(i_type), secondary_me_type),
            (secondary_i_type, str(me_type)),
        ]
        seen_pairs: set[str] = set()

        for candidate_i, candidate_me in candidate_pairs:
            if not candidate_i or not candidate_me:
                continue

            pair_key = f"{candidate_i}::{candidate_me}"
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            profile = get_auto_report(candidate_i, candidate_me)
            serialized_profile = self._serialize_auto_profile(profile)
            if serialized_profile is None:
                continue

            secondary_profiles.append(
                {
                    "i_type": candidate_i,
                    "me_type": candidate_me,
                    "profile": serialized_profile,
                }
            )

        gap_analysis = enriched_data.get("gap_analysis", {})

        context = {
            "i_test": {
                "dominant_type": i_type,
                "scores": i_scores,
                "top_types": i_top_types,
            },
            "me_test": {
                "dominant_type": me_type,
                "scores": me_scores,
                "top_types": me_top_types,
            },
            "gap_analysis": gap_analysis,
            "gap_highlights": self._extract_gap_highlights(gap_analysis),
            "score_insights": {
                "dominant_margin": {
                    "i_test": dominant_i_margin,
                    "me_test": dominant_me_margin,
                },
                "secondary_type": {
                    "i_test": secondary_i_type,
                    "me_test": secondary_me_type,
                },
            },
            "auto_profile": self._serialize_auto_profile(primary_auto_report),
            "secondary_profiles": secondary_profiles,
        }

        user_prompt = WPI_REPORT_USER_TEMPLATE.format(
            context_json=json.dumps(context, ensure_ascii=False, indent=2)
        )

        return [
            {"role": "system", "content": WPI_REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    async def _get_result_for_user(self, user_id: UUID, result_id: UUID) -> ScanResult:
        result = await self.get_by_id(result_id)
        if result is None:
            raise LookupError("WPI 검사 결과를 찾을 수 없습니다")
        if result.user_id != user_id:
            raise PermissionError("접근 권한이 없습니다")
        return result

    async def get_ai_report(self, user_id: UUID, result_id: UUID) -> dict[str, Any]:
        result = await self._get_result_for_user(user_id, result_id)
        state = await self._reconcile_ai_report_state(result)
        return {"result_id": result.id, **state}

    async def enqueue_ai_report_generation(
        self,
        user_id: UUID,
        result_id: UUID,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        result = await self._get_result_for_user(user_id, result_id)
        if result.status != "completed":
            raise ValueError("완료된 검사 결과에서만 AI 리포트를 생성할 수 있습니다")

        enriched_data = self.enrich_with_scores(result.data)
        i_test = enriched_data.get("i_test")
        me_test = enriched_data.get("me_test")
        if not i_test or not me_test:
            raise ValueError(
                "i/me 테스트가 모두 완료되어야 AI 리포트를 생성할 수 있습니다"
            )

        current_state = await self._reconcile_ai_report_state(result)
        current_status = current_state["status"]
        if not force_regenerate and current_status in {"queued", "processing"}:
            return {
                "result_id": result.id,
                **current_state,
                "queued": False,
                "message": "AI 리포트 생성 작업이 이미 진행 중입니다",
            }

        if (
            not force_regenerate
            and current_status == "completed"
            and current_state.get("report_md")
        ):
            return {
                "result_id": result.id,
                **current_state,
                "queued": False,
                "message": "이미 생성된 AI 리포트가 있습니다",
            }

        if force_regenerate:
            await self._clear_wpi_active_job(result.id)

        messages = self._build_ai_report_messages(enriched_data)

        task_queue = get_task_queue()
        loop = asyncio.get_running_loop()
        enqueue_func = partial(
            task_queue.enqueue_wpi_report_job,
            scan_result_id=str(result.id),
            messages=messages,
        )
        job_id = await loop.run_in_executor(None, preserve_otel_context(enqueue_func))

        if not job_id:
            refreshed_state = await self._reconcile_ai_report_state(result)
            return {
                "result_id": result.id,
                **refreshed_state,
                "queued": False,
                "message": "이미 실행 중인 작업이 있어 큐 등록을 건너뛰었습니다",
            }

        next_state = await self._write_ai_report_state(
            result,
            status="queued",
            report_md=None,
            error=None,
            job_id=job_id,
        )
        return {
            "result_id": result.id,
            **next_state,
            "queued": bool(job_id),
            "message": (
                "AI 리포트 생성이 큐에 등록되었습니다"
                if job_id
                else "이미 실행 중인 작업이 있어 큐 등록을 건너뛰었습니다"
            ),
        }

    async def get_by_id(self, result_id: UUID) -> ScanResult | None:
        """검사 결과 ID로 조회."""
        result = await self.repo.get_by_id(result_id)
        if result and result.scan_type != self.SCAN_TYPE:
            return None
        return result

    async def get_by_id_with_scores(
        self, result_id: UUID
    ) -> tuple[ScanResult, dict[str, Any]] | None:
        """검사 결과 ID로 조회하고 점수를 동적으로 계산."""
        result = await self.get_by_id(result_id)
        if result is None:
            return None
        enriched_data = self.enrich_with_scores(result.data)
        return result, enriched_data

    async def get_status(self, user_id: UUID) -> dict[str, Any]:
        """현재 진행 중인 검사 상태 조회."""
        in_progress = await self.repo.get_in_progress(user_id, self.SCAN_TYPE)
        if in_progress:
            data = in_progress.data
            return {
                "has_incomplete": True,
                "in_progress_id": str(in_progress.id),
                "created_at": in_progress.created_at,
                "i_test_completed": data.get("i_test") is not None,
                "me_test_completed": data.get("me_test") is not None,
                "has_profile": False,
            }

        latest = await self.repo.get_latest_by_user(
            user_id, self.SCAN_TYPE, status="completed"
        )
        if latest:
            return {
                "has_incomplete": False,
                "in_progress_id": None,
                "created_at": None,
                "i_test_completed": True,
                "me_test_completed": True,
                "has_profile": True,
            }

        return {
            "has_incomplete": False,
            "in_progress_id": None,
            "created_at": None,
            "i_test_completed": False,
            "me_test_completed": False,
            "has_profile": False,
        }

    async def delete_in_progress(self, user_id: UUID) -> bool:
        """진행 중인 검사 삭제 (새로 시작하기 위해)."""
        in_progress = await self.repo.get_in_progress(user_id, self.SCAN_TYPE)
        if in_progress:
            await self.repo.delete(in_progress)
            await self.session.commit()
            logger.info(
                f"WPI in-progress scan deleted for user {user_id}, result {in_progress.id}"
            )
            return True
        return False

    async def get_history(
        self,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        completed_only: bool = True,
    ) -> tuple[list[ScanResult], int]:
        """검사 이력 조회."""
        status = "completed" if completed_only else None
        results = await self.repo.get_history(
            user_id, scan_type=self.SCAN_TYPE, status=status, limit=limit, offset=offset
        )
        total = await self.repo.count(user_id, scan_type=self.SCAN_TYPE, status=status)
        return results, total

    async def get_latest(self, user_id: UUID) -> ScanResult | None:
        """최신 완료된 검사 조회."""
        return await self.repo.get_latest_by_user(
            user_id, self.SCAN_TYPE, status="completed"
        )

    async def submit_i_test(
        self, user_id: UUID, responses: WpiResponses
    ) -> tuple[ScanResult, dict[str, float], str]:
        """I-Test 응답 제출 (raw_responses만 저장, 점수는 조회 시 계산)."""
        # 진행 중인 검사 확인
        in_progress = await self.repo.get_in_progress(user_id, self.SCAN_TYPE)

        if in_progress and in_progress.data.get("i_test") is not None:
            # 이미 I-Test 완료된 진행 중 검사가 있으면 새로 생성
            in_progress = None

        raw_responses = {
            "rank_1": responses.rank_1,
            "rank_2": responses.rank_2,
            "rank_3": responses.rank_3,
        }

        if in_progress:
            # 기존 진행 중 검사에 I-Test 추가
            data = in_progress.data.copy()
            data["i_test"] = {"raw_responses": raw_responses}
            result = await self.repo.update(in_progress, data=data)
        else:
            # 새 검사 생성
            data = {
                "version": WPI_DATA_VERSION,
                "i_test": {"raw_responses": raw_responses},
                "me_test": None,
            }
            result = await self.repo.create(
                user_id, self.SCAN_TYPE, data, status="in_progress"
            )

        await self.session.commit()

        # 응답용으로 점수 계산 (저장은 안 함)
        scores = self.calculate_scores("i_test", responses)
        dominant = self.get_dominant_type(scores)

        logger.info(
            f"WPI I-Test completed for user {user_id}, result {result.id}, dominant: {dominant}",
            extra={"scores": scores},
        )

        return result, scores, dominant

    async def submit_me_test(
        self, user_id: UUID, responses: WpiResponses
    ) -> tuple[ScanResult, dict[str, float], str, dict[str, dict[str, float | str]]]:
        """Me-Test 응답 제출 (raw_responses만 저장, 점수는 조회 시 계산)."""
        # 진행 중인 검사 조회 (I-Test 완료 필요)
        in_progress = await self.repo.get_in_progress(user_id, self.SCAN_TYPE)
        if in_progress is None or in_progress.data.get("i_test") is None:
            raise ValueError("I-Test를 먼저 완료해야 합니다")

        raw_responses = {
            "rank_1": responses.rank_1,
            "rank_2": responses.rank_2,
            "rank_3": responses.rank_3,
        }

        # 데이터 업데이트 (raw_responses만 저장)
        data = in_progress.data.copy()
        data["me_test"] = {"raw_responses": raw_responses}

        result = await self.repo.update(in_progress, data=data, status="completed")
        await self.session.commit()

        # 응답용으로 점수 계산 (저장은 안 함)
        scores = self.calculate_scores("me_test", responses)
        dominant = self.get_dominant_type(scores)

        # I-Test 점수도 계산해서 GAP 분석
        i_raw = in_progress.data["i_test"]["raw_responses"]
        i_responses = WpiResponses(**i_raw)
        i_scores = self.calculate_scores("i_test", i_responses)
        gap_analysis = self.calculate_gap_analysis(i_scores, scores)

        logger.info(
            f"WPI Me-Test completed for user {user_id}, result {result.id}, dominant: {dominant}",
            extra={"scores": scores, "gap_analysis": gap_analysis},
        )

        return result, scores, dominant, gap_analysis
