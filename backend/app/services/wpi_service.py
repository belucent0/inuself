"""WPI 심리검사 서비스.

채점 로직, GAP 분석, 프로필 관리 등 비즈니스 로직 담당.
ScanResult 범용 테이블 사용.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import logger
from ..db.models import ScanResult
from ..repositories.scan_repository import ScanRepository
from ..schemas.wpi import (
    GAP_AXIS_MAP,
    I_TEST_TYPES,
    ME_TEST_TYPES,
    WpiQuestion,
    WpiResponses,
)


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

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ScanRepository(session)
        self._i_test_data: list[dict] | None = None
        self._me_test_data: list[dict] | None = None

    def _load_questions(self, test_type: Literal["i_test", "me_test"]) -> list[dict]:
        """JSON에서 문항 데이터 로드 (캐싱)."""
        if test_type == "i_test":
            if self._i_test_data is None:
                with open(self.I_TEST_FILE, encoding="utf-8") as f:
                    self._i_test_data = json.load(f)
            return self._i_test_data
        else:
            if self._me_test_data is None:
                with open(self.ME_TEST_FILE, encoding="utf-8") as f:
                    self._me_test_data = json.load(f)
            return self._me_test_data

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
    ) -> dict:
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

    def enrich_with_scores(self, data: dict) -> dict:
        """raw_responses에서 점수를 동적으로 계산하여 데이터에 추가."""
        enriched = data.copy()

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
            enriched.get("i_test") and enriched["i_test"].get("scores")
            and enriched.get("me_test") and enriched["me_test"].get("scores")
        ):
            enriched["gap_analysis"] = self.calculate_gap_analysis(
                enriched["i_test"]["scores"],
                enriched["me_test"]["scores"],
            )

        return enriched

    async def get_by_id(self, result_id: UUID) -> ScanResult | None:
        """검사 결과 ID로 조회."""
        result = await self.repo.get_by_id(result_id)
        if result and result.scan_type != self.SCAN_TYPE:
            return None
        return result

    async def get_by_id_with_scores(self, result_id: UUID) -> tuple[ScanResult, dict] | None:
        """검사 결과 ID로 조회하고 점수를 동적으로 계산."""
        result = await self.get_by_id(result_id)
        if result is None:
            return None
        enriched_data = self.enrich_with_scores(result.data)
        return result, enriched_data

    async def get_status(self, user_id: UUID) -> dict:
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

        latest = await self.repo.get_latest_by_user(user_id, self.SCAN_TYPE, status="completed")
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
            logger.info(f"WPI in-progress scan deleted for user {user_id}, result {in_progress.id}")
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
        return await self.repo.get_latest_by_user(user_id, self.SCAN_TYPE, status="completed")

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
            result = await self.repo.create(user_id, self.SCAN_TYPE, data, status="in_progress")

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
    ) -> tuple[ScanResult, dict[str, float], str, dict]:
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
