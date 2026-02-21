"""datetime_tool 단위 테스트.

get_current_datetime()의 반환 형식, 기본 타임존, 결정적 동작을 검증합니다.
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

# conftest.py가 sys.path를 먼저 설정합니다.
from app.agents.tools.datetime_tool import get_current_datetime


class TestGetCurrentDatetimeFormat:
    """반환 형식 검증."""

    def test_format_matches_pattern(self) -> None:
        """반환값이 'YYYY년 MM월 DD일 (X요일)' 패턴인지 정규식으로 검증합니다."""
        result = get_current_datetime()
        pattern = r"^\d{4}년 \d{2}월 \d{2}일 \([가-힣]+요일\)$"
        assert re.match(pattern, result), (
            f"'{result}'이(가) 'YYYY년 MM월 DD일 (X요일)' 형식과 일치하지 않습니다."
        )

    def test_year_is_four_digits(self) -> None:
        """연도가 4자리인지 확인합니다."""
        result = get_current_datetime()
        year_str = result.split("년")[0]
        assert len(year_str) == 4
        assert year_str.isdigit()

    def test_weekday_is_valid_korean(self) -> None:
        """요일이 올바른 한국어 형식인지 확인합니다."""
        valid_weekdays = {"월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"}
        result = get_current_datetime()
        # "(월요일)" 형태에서 괄호 제거
        weekday_part = result.split("(")[1].rstrip(")")
        assert weekday_part in valid_weekdays, (
            f"'{weekday_part}'은(는) 유효한 한국어 요일이 아닙니다."
        )

    def test_returns_string(self) -> None:
        """반환 타입이 str인지 확인합니다."""
        result = get_current_datetime()
        assert isinstance(result, str)


class TestGetCurrentDatetimeTimezone:
    """타임존 동작 검증."""

    def test_default_timezone_is_seoul(self) -> None:
        """기본 타임존이 Asia/Seoul인지 확인합니다.

        Asia/Seoul과 UTC는 9시간 차이가 있으므로,
        고정 UTC 시간으로 mock하여 Seoul 시각이 올바른지 검증합니다.
        """
        # UTC 2026-01-01 00:00:00 → Seoul은 2026-01-01 09:00:00 (목요일)
        fixed_utc = datetime(2026, 1, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))

        with patch("app.agents.tools.datetime_tool.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_utc.astimezone(ZoneInfo("Asia/Seoul"))
            result = get_current_datetime()

        assert "2026년" in result
        assert "01월" in result
        assert "01일" in result
        assert "목요일" in result

    def test_custom_timezone_utc(self) -> None:
        """UTC 타임존을 명시하면 해당 시각이 반환되는지 확인합니다."""
        # UTC 2026-01-01 15:00:00 → UTC 날짜는 01월 01일
        fixed_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=ZoneInfo("UTC"))

        with patch("app.agents.tools.datetime_tool.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_utc
            result = get_current_datetime(timezone="UTC")

        assert "2026년" in result
        assert "01월 01일" in result


class TestGetCurrentDatetimeWithFixedTime:
    """mock을 통한 고정 시간 테스트 (결정적 동작 보장)."""

    def test_fixed_saturday(self) -> None:
        """2026-02-21 (토요일) Asia/Seoul 고정 mock 테스트."""
        fixed_time = datetime(2026, 2, 21, 12, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        with patch("app.agents.tools.datetime_tool.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            result = get_current_datetime()

        assert result == "2026년 02월 21일 (토요일)"

    def test_fixed_monday(self) -> None:
        """2026-03-02 (월요일) Asia/Seoul 고정 mock 테스트."""
        fixed_time = datetime(2026, 3, 2, 9, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        with patch("app.agents.tools.datetime_tool.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            result = get_current_datetime()

        assert result == "2026년 03월 02일 (월요일)"

    @pytest.mark.parametrize(
        "date_str, weekday_name",
        [
            ("2026-01-05", "월요일"),  # 2026-01-05는 월요일
            ("2026-01-06", "화요일"),
            ("2026-01-07", "수요일"),
            ("2026-01-08", "목요일"),
            ("2026-01-09", "금요일"),
            ("2026-01-10", "토요일"),
            ("2026-01-11", "일요일"),
        ],
    )
    def test_all_weekdays(self, date_str: str, weekday_name: str) -> None:
        """7개 요일이 모두 올바르게 한국어로 변환되는지 확인합니다."""
        year, month, day = map(int, date_str.split("-"))
        fixed_time = datetime(year, month, day, 12, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        with patch("app.agents.tools.datetime_tool.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            result = get_current_datetime()

        assert weekday_name in result, (
            f"{date_str}는 {weekday_name}이어야 하지만 결과: '{result}'"
        )
