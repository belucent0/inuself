"""WPI 심리검사 서비스.

채점 로직, GAP 분석, 프로필 관리 등 비즈니스 로직 담당.
ScanResult 범용 테이블 사용.
"""

from __future__ import annotations

import json
import os
import random
import asyncio
from io import BytesIO
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logging as app_logging
from ..core.config import get_settings
from ..core.storage import upload_fileobj
from ..db.models import ScanResult
from ..db.session import AsyncSessionLocal
try:
    from ..prompts.wpi_report import (
        WPI_REPORT_SYSTEM_PROMPT,
        WPI_REPORT_USER_TEMPLATE,
        WPI_SECTION6_SYSTEM_PROMPT,
        WPI_SECTION6_USER_TEMPLATE,
    )
except ImportError:
    from ..prompts.wpi_report_example import (  # type: ignore[no-redef]
        WPI_REPORT_SYSTEM_PROMPT,
        WPI_REPORT_USER_TEMPLATE,
    )
    WPI_SECTION6_SYSTEM_PROMPT = WPI_REPORT_SYSTEM_PROMPT
    WPI_SECTION6_USER_TEMPLATE = WPI_REPORT_USER_TEMPLATE
from ..repositories.scan_repository import ScanRepository
from ..schemas.wpi import (
    GAP_AXIS_MAP,
    I_TEST_TYPES,
    ME_TEST_TYPES,
    WpiQuestion,
    WpiResponses,
)
from .litellm_client import request_litellm_completion
from .wpi_profile_parser import load_combination_text, load_default_text


# WPI 데이터 스키마 버전
# v1: 초기 구조 (2026-02-09)
WPI_DATA_VERSION = 1

I_TYPE_KR_LABELS = {
    "Realist": "리얼리스트",
    "Romanticist": "로맨티스트",
    "Humanist": "휴머니스트",
    "Idealist": "아이디얼리스트",
    "Agent": "에이전트",
}

ME_TYPE_KR_LABELS = {
    "Relation": "릴레이션",
    "Trust": "트러스트",
    "Manual": "매뉴얼",
    "Self": "셀프",
    "Culture": "컬처",
}

AXIS_KR_LABELS = {
    "relation_recognition": "관계-인지 축",
    "emotion_trust": "정서-신뢰 축",
    "social_control": "사회성-통제 축",
    "independence_self": "독립성-자기축",
    "achievement_culture": "성취-문화 축",
}

GAP_OVERSHOOTING_THRESHOLD = 10
MULTI_TYPE_MARGIN = 7

PROBLEMATIC_MULTI_COMBOS: list[tuple[set[str], str]] = [
    ({"Idealist", "Romanticist"}, "아이디얼리스트+로맨티스트"),
    ({"Humanist", "Romanticist"}, "휴머니스트+로맨티스트"),
    ({"Romanticist", "Agent"}, "로맨티스트+에이전트"),
    ({"Realist", "Humanist", "Agent"}, "리얼리스트+휴머니스트+에이전트"),
]

logger = app_logging.logger


def _build_question_data_dir_candidates() -> list[Path]:
    override = os.getenv("WPI_QUESTION_DATA_DIR", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    current_file = Path(__file__).resolve()
    parents = current_file.parents

    # 1) backend/data/wpi (현재 compose 볼륨 기준)
    if len(parents) > 2:
        candidates.append(parents[2] / "data" / "wpi")

    # 2) backend/app/data/wpi (레거시 경로)
    if len(parents) > 1:
        candidates.append(parents[1] / "data" / "wpi")

    # 3) 컨테이너 런타임 후보
    candidates.append(Path("/app/data/wpi"))
    candidates.append(Path("/app/app/data/wpi"))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique


def _resolve_question_data_dir() -> Path:
    candidates = _build_question_data_dir_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if candidates:
        return candidates[0]
    return Path("data/wpi")


class WpiService:
    """WPI 검사 채점 및 프로필 관리 서비스."""

    SCAN_TYPE = "wpi"

    # 순위별 가중치 (1순위*7, 2순위*5, 3순위*3)
    RANK_WEIGHTS = {1: 7, 2: 5, 3: 3}
    AI_REPORT_STATUS_VALUES = {"idle", "processing", "completed", "failed"}

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ScanRepository(session)
        self._i_test_data: list[dict[str, Any]] | None = None
        self._me_test_data: list[dict[str, Any]] | None = None
        self.settings = get_settings()
        self.question_data_dir = _resolve_question_data_dir()
        self.i_test_file = self.question_data_dir / "i-test-question.json"
        self.me_test_file = self.question_data_dir / "me-test-question.json"

    def _refresh_question_file_paths(self) -> None:
        self.question_data_dir = _resolve_question_data_dir()
        self.i_test_file = self.question_data_dir / "i-test-question.json"
        self.me_test_file = self.question_data_dir / "me-test-question.json"

    def _load_questions(
        self, test_type: Literal["i_test", "me_test"]
    ) -> list[dict[str, Any]]:
        """JSON에서 문항 데이터 로드 (캐싱)."""
        if test_type == "i_test":
            if self._i_test_data is None:
                if not self.i_test_file.exists():
                    self._refresh_question_file_paths()
                with open(self.i_test_file, encoding="utf-8") as f:
                    self._i_test_data = json.load(f)
            i_data = self._i_test_data
            if i_data is None:
                raise RuntimeError("Failed to load i-test questions")
            return i_data
        else:
            if self._me_test_data is None:
                if not self.me_test_file.exists():
                    self._refresh_question_file_paths()
                with open(self.me_test_file, encoding="utf-8") as f:
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
            i_test_payload = enriched.get("i_test")
            if isinstance(i_test_payload, dict) and not self._has_complete_scores(
                i_test_payload, I_TEST_TYPES
            ):
                raw = i_test_payload.get("raw_responses")
                if raw:
                    responses = WpiResponses(**raw)
                    scores = self.calculate_scores("i_test", responses)
                    dominant = self.get_dominant_type(scores)
                    i_test_payload["scores"] = scores
                    i_test_payload["dominant_type"] = dominant

            # Me-Test 점수 계산
            me_test_payload = enriched.get("me_test")
            if isinstance(me_test_payload, dict) and not self._has_complete_scores(
                me_test_payload, ME_TEST_TYPES
            ):
                raw = me_test_payload.get("raw_responses")
                if raw:
                    responses = WpiResponses(**raw)
                    scores = self.calculate_scores("me_test", responses)
                    dominant = self.get_dominant_type(scores)
                    me_test_payload["scores"] = scores
                    me_test_payload["dominant_type"] = dominant

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
                "WPI question file missing; returning raw scan data without score enrichment: "
                "dir=%s i_file=%s me_file=%s error=%s",
                self.question_data_dir,
                self.i_test_file,
                self.me_test_file,
                exc,
            )
            return enriched

        return enriched

    def _has_complete_scores(
        self,
        payload: dict[str, Any],
        expected_types: list[str],
    ) -> bool:
        scores_obj = payload.get("scores")
        if not isinstance(scores_obj, dict):
            return False

        normalized_scores: dict[str, float] = {}
        for type_name in expected_types:
            raw_value = scores_obj.get(type_name)
            if raw_value is None:
                return False
            try:
                normalized_scores[type_name] = float(raw_value)
            except (TypeError, ValueError):
                return False

        payload["scores"] = normalized_scores
        dominant_type = payload.get("dominant_type")
        if dominant_type not in normalized_scores:
            payload["dominant_type"] = self.get_dominant_type(normalized_scores)

        return True

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

    def _build_personalization_summary(
        self,
        *,
        i_type: str,
        me_type: str,
        i_scores: dict[str, float],
        me_scores: dict[str, float],
        i_top_types: list[dict[str, float | str]],
        me_top_types: list[dict[str, float | str]],
        dominant_i_margin: float | None,
        dominant_me_margin: float | None,
        gap_highlights: list[dict[str, Any]],
        primary_auto_report: Any,
    ) -> tuple[dict[str, Any], list[str]]:
        dominant_i_score = float(i_scores.get(i_type, 0.0))
        dominant_me_score = float(me_scores.get(me_type, 0.0))

        secondary_i_type = str(i_top_types[1]["type"]) if len(i_top_types) > 1 else None
        secondary_me_type = (
            str(me_top_types[1]["type"]) if len(me_top_types) > 1 else None
        )

        summary = {
            "dominant_pair": {
                "i_type": i_type,
                "i_type_kr": I_TYPE_KR_LABELS.get(i_type, i_type),
                "i_score": dominant_i_score,
                "i_margin": dominant_i_margin,
                "me_type": me_type,
                "me_type_kr": ME_TYPE_KR_LABELS.get(me_type, me_type),
                "me_score": dominant_me_score,
                "me_margin": dominant_me_margin,
            },
            "secondary_pair": {
                "i_type": secondary_i_type,
                "i_type_kr": I_TYPE_KR_LABELS.get(secondary_i_type, secondary_i_type)
                if secondary_i_type
                else None,
                "i_score": (
                    float(i_scores.get(secondary_i_type, 0.0))
                    if secondary_i_type
                    else None
                ),
                "me_type": secondary_me_type,
                "me_type_kr": ME_TYPE_KR_LABELS.get(
                    secondary_me_type, secondary_me_type
                )
                if secondary_me_type
                else None,
                "me_score": (
                    float(me_scores.get(secondary_me_type, 0.0))
                    if secondary_me_type
                    else None
                ),
            },
            "focus_axes": [
                {
                    "axis": item["axis"],
                    "axis_kr": AXIS_KR_LABELS.get(item["axis"], item["axis"]),
                    "i_type": item["i_type"],
                    "me_type": item["me_type"],
                    "gap": float(item["gap"]),
                    "direction": item["direction"],
                }
                for item in gap_highlights
            ],
        }

        grounding_facts: list[str] = []
        grounding_facts.append(
            "우세형 조합: "
            f"I-Test {i_type}({dominant_i_score:.1f}), "
            f"Me-Test {me_type}({dominant_me_score:.1f})"
        )

        if secondary_i_type:
            grounding_facts.append(
                "I-Test 2순위: "
                f"{secondary_i_type}({float(i_scores.get(secondary_i_type, 0.0)):.1f})"
            )
        if secondary_me_type:
            grounding_facts.append(
                "Me-Test 2순위: "
                f"{secondary_me_type}({float(me_scores.get(secondary_me_type, 0.0)):.1f})"
            )

        if dominant_i_margin is not None:
            grounding_facts.append(f"I-Test 우세 마진: {dominant_i_margin:.1f}")
        if dominant_me_margin is not None:
            grounding_facts.append(f"Me-Test 우세 마진: {dominant_me_margin:.1f}")

        for item in gap_highlights:
            gap_value = float(item["gap"])
            direction = "I-Test 우세" if gap_value > 0 else "Me-Test 우세"
            if gap_value == 0:
                direction = "균형"
            grounding_facts.append(
                "주요 갭 축 "
                f"{AXIS_KR_LABELS.get(str(item['axis']), str(item['axis']))}: "
                f"{item['i_type']} vs {item['me_type']} ({gap_value:+.1f}, {direction})"
            )

        if primary_auto_report:
            if primary_auto_report.basic_need:
                grounding_facts.append(
                    f"우세 조합 기본욕구: {primary_auto_report.basic_need}"
                )
            if primary_auto_report.weaknesses:
                grounding_facts.append(
                    f"우세 조합 주의 포인트: {primary_auto_report.weaknesses}"
                )

        return summary, grounding_facts

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

    def _upload_ai_report_payload(self, result_id: UUID, report_md: str) -> str:
        payload = {
            "scan_result_id": str(result_id),
            "raw_response": report_md,
            "generation_mode": "two_pass",
        }
        result_s3_key = f"results/wpi_report/{result_id}/{uuid4().hex}.json"
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        upload_fileobj(
            BytesIO(raw),
            key=result_s3_key,
            content_type="application/json",
        )
        return result_s3_key

    async def _generate_ai_report_markdown(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        wpi_model = os.getenv("WPI_REPORT_MODEL", "").strip()
        if not wpi_model:
            wpi_model = self.settings.ai_gateway_model_summarize

        request_timeout_seconds = float(
            os.getenv("WPI_REPORT_LLM_REQUEST_TIMEOUT_SECONDS", "45")
        )
        busy_max_seconds = int(os.getenv("WPI_REPORT_LLM_BUSY_MAX_SECONDS", "90"))
        retry_interval_seconds = int(
            os.getenv("WPI_REPORT_LLM_RETRY_INTERVAL_SECONDS", "3")
        )
        single_prompt_timeout_seconds = int(
            os.getenv("WPI_REPORT_SINGLE_PROMPT_TIMEOUT_SECONDS", "180")
        )

        llm_call = lambda: request_litellm_completion(
            settings=self.settings,
            messages=messages,
            model=wpi_model,
            request_timeout_seconds=request_timeout_seconds,
            max_retry_time=busy_max_seconds,
            retry_interval=retry_interval_seconds,
        )
        return await asyncio.wait_for(
            asyncio.to_thread(llm_call),
            timeout=single_prompt_timeout_seconds,
        )

    def _build_section6_messages(
        self,
        score_profile_json: str,
        collected_texts: str,
        preceding_sections: str,
    ) -> list[dict[str, str]]:
        user_prompt = WPI_SECTION6_USER_TEMPLATE.format(
            score_profile_json=score_profile_json,
            collected_texts=collected_texts,
            preceding_sections=preceding_sections,
        )
        return [
            {"role": "system", "content": WPI_SECTION6_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    async def _execute_ai_report_generation_by_id(
        self,
        result_id: UUID,
        pass1_messages: list[dict[str, str]],
        score_profile_json: str,
        collected_texts: str,
    ) -> None:
        result = await self.repo.get_by_id(result_id)
        if result is None:
            logger.warning("WPI AI report target not found: result_id=%s", result_id)
            return

        try:
            # Pass 1: 섹션 1~5 생성
            sections_1_5 = await self._generate_ai_report_markdown(pass1_messages)
            logger.info("WPI AI report Pass 1 done: result_id=%s", result_id)

            # Pass 2: 섹션 6 생성 (앞 섹션 참조)
            pass2_messages = self._build_section6_messages(
                score_profile_json=score_profile_json,
                collected_texts=collected_texts,
                preceding_sections=sections_1_5,
            )
            section_6 = await self._generate_ai_report_markdown(pass2_messages)
            logger.info("WPI AI report Pass 2 done: result_id=%s", result_id)

            report_md = sections_1_5 + "\n\n" + section_6
            result_s3_key = self._upload_ai_report_payload(result.id, report_md)
            logger.info(
                "WPI AI report generated: result_id=%s, result_s3_key=%s",
                result.id,
                result_s3_key,
            )
            await self._write_ai_report_state(
                result,
                status="completed",
                report_md=report_md,
                error=None,
                job_id=None,
            )
        except Exception as exc:
            logger.exception("WPI AI report generation failed: result_id=%s", result_id)
            await self._write_ai_report_state(
                result,
                status="failed",
                report_md=None,
                error=str(exc),
                job_id=None,
            )

    @classmethod
    async def _run_ai_report_generation_task(
        cls,
        result_id: UUID,
        pass1_messages: list[dict[str, str]],
        score_profile_json: str,
        collected_texts: str,
    ) -> None:
        async with AsyncSessionLocal() as session:
            service = cls(session)
            await service._execute_ai_report_generation_by_id(
                result_id, pass1_messages, score_profile_json, collected_texts
            )

    # I-type → 페어링 Me-type 역방향 매핑
    _I_TO_PAIRED_ME: dict[str, str] = {
        i_t: m_t for _, (i_t, m_t) in GAP_AXIS_MAP.items()
    }

    def _analyze_score_profile(
        self,
        i_type: str,
        me_type: str,
        i_scores: dict[str, float],
        me_scores: dict[str, float],
        i_top_types: list[dict[str, float | str]],
        me_top_types: list[dict[str, float | str]],
        dominant_i_margin: float | None,
        dominant_me_margin: float | None,
    ) -> dict[str, Any]:
        """점수 프로파일 분석 — 마음 읽기 레시피 결정."""
        paired_me_type = self._I_TO_PAIRED_ME.get(i_type)

        paired_gap: float | None = None
        paired_gap_direction = "balanced"
        if paired_me_type:
            i_val = i_scores.get(i_type, 0.0)
            me_val = me_scores.get(paired_me_type, 0.0)
            paired_gap = i_val - me_val
            if paired_gap > 5:
                paired_gap_direction = "i_high"
            elif paired_gap < -5:
                paired_gap_direction = "me_high"

        # 5축 전체 I-Me 갭 분석
        all_axis_gaps = []
        for axis_name, (axis_i_type, axis_me_type) in GAP_AXIS_MAP.items():
            i_val = i_scores.get(axis_i_type, 0.0)
            me_val = me_scores.get(axis_me_type, 0.0)
            gap_val = i_val - me_val
            abs_gap_val = abs(gap_val)
            direction = "balanced"
            if gap_val > 5:
                direction = "i_high"
            elif gap_val < -5:
                direction = "me_high"
            all_axis_gaps.append({
                "i_type_kr": I_TYPE_KR_LABELS.get(axis_i_type, axis_i_type),
                "me_type_kr": ME_TYPE_KR_LABELS.get(axis_me_type, axis_me_type),
                "gap": round(gap_val, 1),
                "abs_gap": round(abs_gap_val, 1),
                "gap_direction": direction,
                "is_primary_axis": axis_i_type == i_type,
                "is_overshooting": abs_gap_val > GAP_OVERSHOOTING_THRESHOLD,
            })
        all_axis_gaps.sort(key=lambda x: x["abs_gap"], reverse=True)

        secondary_i_type = str(i_top_types[1]["type"]) if len(i_top_types) > 1 else None
        secondary_i_score = float(i_top_types[1]["score"]) if len(i_top_types) > 1 else None

        i_sorted = sorted(i_scores.items(), key=lambda x: x[1])
        me_sorted = sorted(me_scores.items(), key=lambda x: x[1])
        i_lowest = i_sorted[0][0] if i_sorted else None
        me_lowest = me_sorted[0][0] if me_sorted else None

        is_dual_i_type = dominant_i_margin is not None and dominant_i_margin < 5

        # 3위 I-type 정보
        tertiary_i_type = str(i_top_types[2]["type"]) if len(i_top_types) > 2 else None
        tertiary_i_score = float(i_top_types[2]["score"]) if len(i_top_types) > 2 else None
        i_secondary_margin = (
            float(i_top_types[1]["score"]) - float(i_top_types[2]["score"])
            if len(i_top_types) > 2 else None
        )

        # Multi-type 감지 (MULTI_TYPE_MARGIN 이내)
        primary_score = float(i_top_types[0]["score"])
        multi_types: list[str] = []
        for entry in i_top_types:
            if primary_score - float(entry["score"]) <= MULTI_TYPE_MARGIN:
                multi_types.append(str(entry["type"]))
            else:
                break

        multi_i_type_count = len(multi_types)
        is_multi_i_type = multi_i_type_count >= 2
        is_triple_i_type = multi_i_type_count >= 3

        # 문제 조합 감지
        multi_set = set(multi_types)
        has_problematic = False
        problematic_label = None
        for combo_set, label in PROBLEMATIC_MULTI_COMBOS:
            if combo_set.issubset(multi_set):
                has_problematic = True
                problematic_label = label
                break

        # 다중형 + 갭 동시 존재
        multi_type_with_gap = is_multi_i_type and any(
            g["is_overshooting"] for g in all_axis_gaps
        )

        # --- 안정형(Stability) 감지 ---
        balanced_axis_count = sum(
            1 for g in all_axis_gaps if g["gap_direction"] == "balanced"
        )
        max_abs_gap = max(g["abs_gap"] for g in all_axis_gaps) if all_axis_gaps else 0.0

        # 안정형: 모든 축이 balanced + 단일 자기평가형
        is_overall_balanced = (
            balanced_axis_count == 5
            and not is_multi_i_type
        )

        # 준안정형: 4축 이상 balanced + overshooting 없음 + 단일형
        is_near_balanced = (
            balanced_axis_count >= 4
            and not any(g["is_overshooting"] for g in all_axis_gaps)
            and not is_multi_i_type
        )

        return {
            "i_primary": i_type,
            "i_primary_score": i_scores.get(i_type, 0.0),
            "i_primary_kr": I_TYPE_KR_LABELS.get(i_type, i_type),
            "i_secondary": secondary_i_type,
            "i_secondary_score": secondary_i_score,
            "i_secondary_kr": I_TYPE_KR_LABELS.get(secondary_i_type, secondary_i_type) if secondary_i_type else None,
            "i_margin": dominant_i_margin,
            "i_lowest": i_lowest,
            "i_lowest_kr": I_TYPE_KR_LABELS.get(i_lowest, i_lowest) if i_lowest else None,
            "me_primary": me_type,
            "me_primary_score": me_scores.get(me_type, 0.0),
            "me_primary_kr": ME_TYPE_KR_LABELS.get(me_type, me_type),
            "me_margin": dominant_me_margin,
            "me_lowest": me_lowest,
            "me_lowest_kr": ME_TYPE_KR_LABELS.get(me_lowest, me_lowest) if me_lowest else None,
            "paired_me_type": paired_me_type,
            "paired_me_type_kr": ME_TYPE_KR_LABELS.get(paired_me_type, paired_me_type) if paired_me_type else None,
            "paired_gap": paired_gap,
            "paired_gap_direction": paired_gap_direction,
            "is_dual_i_type": is_dual_i_type,
            "i_scores_all": dict(i_scores),
            "me_scores_all": dict(me_scores),
            "all_axis_gaps": all_axis_gaps,
            "i_tertiary": tertiary_i_type,
            "i_tertiary_score": tertiary_i_score,
            "i_tertiary_kr": I_TYPE_KR_LABELS.get(tertiary_i_type, tertiary_i_type) if tertiary_i_type else None,
            "i_secondary_margin": i_secondary_margin,
            "multi_i_type_count": multi_i_type_count,
            "multi_i_types": multi_types,
            "multi_i_types_kr": [I_TYPE_KR_LABELS.get(t, t) for t in multi_types],
            "is_multi_i_type": is_multi_i_type,
            "is_triple_i_type": is_triple_i_type,
            "has_problematic_multi_combo": has_problematic,
            "problematic_combo_label": problematic_label,
            "multi_type_with_gap": multi_type_with_gap,
            "balanced_axis_count": balanced_axis_count,
            "max_abs_gap": max_abs_gap,
            "is_overall_balanced": is_overall_balanced,
            "is_near_balanced": is_near_balanced,
        }

    def _build_ai_report_context(self, score_profile: dict[str, Any]) -> str:
        """레시피에 따라 원재료 텍스트를 수집하여 하나의 컨텍스트 문자열로 구성."""
        i_type = score_profile["i_primary"]
        me_type = score_profile["me_primary"]
        secondary_i_type: str | None = score_profile.get("i_secondary")
        paired_me_type: str | None = score_profile.get("paired_me_type")
        is_dual = score_profile.get("is_dual_i_type", False)

        i_lower = i_type.lower()
        sections: list[str] = []

        # 1. 항상 포함: 1위 I-type default.txt
        default_text = load_default_text(i_lower)
        if default_text:
            sections.append(
                f"[자기평가 우세형: {score_profile.get('i_primary_kr', i_type)}]\n{default_text}"
            )

        # 2. 항상 포함: 1위 I-type × paired Me-type 조합
        if paired_me_type:
            paired_text = load_combination_text(i_lower, paired_me_type.lower())
            if paired_text:
                sections.append(
                    f"[페어링 축 해석: {score_profile.get('i_primary_kr', i_type)} × "
                    f"{score_profile.get('paired_me_type_kr', paired_me_type)}]\n{paired_text}"
                )

        # 3. 조건부: 타인평가 우세형이 페어링 축과 다를 때
        if me_type and paired_me_type and me_type.lower() != paired_me_type.lower():
            dominant_me_text = load_combination_text(i_lower, me_type.lower())
            if dominant_me_text:
                sections.append(
                    f"[타인평가 우세형 해석: {score_profile.get('i_primary_kr', i_type)} × "
                    f"{score_profile.get('me_primary_kr', me_type)}]\n{dominant_me_text}"
                )

        # 4. 조건부: dual I-type일 때 2위 I-type default + 조합
        if is_dual and secondary_i_type:
            sec_lower = secondary_i_type.lower()
            sec_default = load_default_text(sec_lower)
            if sec_default:
                sections.append(
                    f"[2차 자기평가: {score_profile.get('i_secondary_kr', secondary_i_type)}]\n{sec_default}"
                )
            if paired_me_type:
                sec_paired = load_combination_text(sec_lower, paired_me_type.lower())
                if sec_paired:
                    sections.append(
                        f"[2차 자기평가 × 페어링 축: {score_profile.get('i_secondary_kr', secondary_i_type)} × "
                        f"{score_profile.get('paired_me_type_kr', paired_me_type)}]\n{sec_paired}"
                    )

        return "\n\n---\n\n".join(sections) if sections else "원재료 텍스트를 찾을 수 없습니다."

    def _build_ai_report_messages(
        self, enriched_data: dict[str, Any]
    ) -> tuple[list[dict[str, str]], str, str]:
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

        i_top_types = self._sort_scores(i_scores, top_k=5)
        me_top_types = self._sort_scores(me_scores, top_k=5)

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

        score_profile = self._analyze_score_profile(
            i_type=str(i_type),
            me_type=str(me_type),
            i_scores=i_scores,
            me_scores=me_scores,
            i_top_types=i_top_types,
            me_top_types=me_top_types,
            dominant_i_margin=dominant_i_margin,
            dominant_me_margin=dominant_me_margin,
        )

        collected_texts = self._build_ai_report_context(score_profile)
        score_profile_json = json.dumps(score_profile, ensure_ascii=False, indent=2)

        user_prompt = WPI_REPORT_USER_TEMPLATE.format(
            score_profile_json=score_profile_json,
            collected_texts=collected_texts,
        )

        messages = [
            {"role": "system", "content": WPI_REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        return messages, score_profile_json, collected_texts

    async def _get_result_for_user(self, user_id: UUID, result_id: UUID) -> ScanResult:
        result = await self.get_by_id(result_id)
        if result is None:
            raise LookupError("WPI 검사 결과를 찾을 수 없습니다")
        if result.user_id != user_id:
            raise PermissionError("접근 권한이 없습니다")
        return result

    async def get_ai_report(self, user_id: UUID, result_id: UUID) -> dict[str, Any]:
        result = await self._get_result_for_user(user_id, result_id)
        state = self._read_ai_report_state(result.data)
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

        current_state = self._read_ai_report_state(result.data)
        current_status = current_state["status"]
        if not force_regenerate and current_status == "processing":
            return {
                "result_id": result.id,
                **current_state,
                "started": False,
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
                "started": False,
                "message": "이미 생성된 AI 리포트가 있습니다",
            }

        pass1_messages, score_profile_json, collected_texts = self._build_ai_report_messages(enriched_data)
        next_state = await self._write_ai_report_state(
            result,
            status="processing",
            report_md=None,
            error=None,
            job_id=None,
        )
        asyncio.create_task(
            self._run_ai_report_generation_task(
                result.id, pass1_messages, score_profile_json, collected_texts
            )
        )
        return {
            "result_id": result.id,
            **next_state,
            "started": True,
            "message": "AI 리포트 생성을 시작했습니다",
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
        """I-Test 응답 제출."""
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

        scores = self.calculate_scores("i_test", responses)
        dominant = self.get_dominant_type(scores)

        if in_progress:
            # 기존 진행 중 검사에 I-Test 추가
            data = in_progress.data.copy()
            data["i_test"] = {
                "raw_responses": raw_responses,
                "scores": scores,
                "dominant_type": dominant,
            }
            result = await self.repo.update(in_progress, data=data)
        else:
            # 새 검사 생성
            data = {
                "version": WPI_DATA_VERSION,
                "i_test": {
                    "raw_responses": raw_responses,
                    "scores": scores,
                    "dominant_type": dominant,
                },
                "me_test": None,
            }
            result = await self.repo.create(
                user_id, self.SCAN_TYPE, data, status="in_progress"
            )

        await self.session.commit()

        logger.info(
            f"WPI I-Test completed for user {user_id}, result {result.id}, dominant: {dominant}",
            extra={"scores": scores},
        )

        return result, scores, dominant

    async def submit_me_test(
        self, user_id: UUID, responses: WpiResponses
    ) -> tuple[ScanResult, dict[str, float], str, dict[str, dict[str, float | str]]]:
        """Me-Test 응답 제출."""
        # 진행 중인 검사 조회 (I-Test 완료 필요)
        in_progress = await self.repo.get_in_progress(user_id, self.SCAN_TYPE)
        if in_progress is None or in_progress.data.get("i_test") is None:
            raise ValueError("I-Test를 먼저 완료해야 합니다")

        raw_responses = {
            "rank_1": responses.rank_1,
            "rank_2": responses.rank_2,
            "rank_3": responses.rank_3,
        }

        scores = self.calculate_scores("me_test", responses)
        dominant = self.get_dominant_type(scores)

        # 데이터 업데이트 (raw_responses만 저장)
        data = in_progress.data.copy()
        data["me_test"] = {
            "raw_responses": raw_responses,
            "scores": scores,
            "dominant_type": dominant,
        }

        result = await self.repo.update(in_progress, data=data, status="completed")
        await self.session.commit()

        # I-Test 점수도 계산해서 GAP 분석
        i_test_payload = in_progress.data.get("i_test")
        i_scores: dict[str, float] | None = None
        if isinstance(i_test_payload, dict) and self._has_complete_scores(
            i_test_payload, I_TEST_TYPES
        ):
            i_scores_obj = i_test_payload.get("scores")
            if isinstance(i_scores_obj, dict):
                i_scores = {
                    str(type_name): float(score)
                    for type_name, score in i_scores_obj.items()
                }

        if i_scores is None:
            i_raw = in_progress.data["i_test"]["raw_responses"]
            i_responses = WpiResponses(**i_raw)
            i_scores = self.calculate_scores("i_test", i_responses)

        gap_analysis = self.calculate_gap_analysis(i_scores, scores)

        logger.info(
            f"WPI Me-Test completed for user {user_id}, result {result.id}, dominant: {dominant}",
            extra={"scores": scores, "gap_analysis": gap_analysis},
        )

        return result, scores, dominant, gap_analysis
