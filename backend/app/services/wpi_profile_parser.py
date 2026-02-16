"""WPI 조합 유형 자동 리포트 파서.

`docs/wpis` 디렉토리의 I-Test / Me-Test 조합 텍스트를 읽어
성향 보고서 응답 객체로 변환한다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..core.logging import logger
from ..schemas.wpi import I_TEST_TYPES, ME_TEST_TYPES


def _resolve_wpi_report_root() -> Path:
    """WPI 리포트 루트 경로를 런타임 환경에 맞게 탐색한다."""

    override = os.getenv("WPI_REPORT_ROOT")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    current_file = Path(__file__).resolve()
    parents = current_file.parents

    # 1) repo root/docs/wpis (일반 로컬 실행)
    if len(parents) > 3:
        candidates.append(parents[3] / "docs" / "wpis")

    # 2) backend/docs/wpis (과거 구조 호환)
    if len(parents) > 2:
        candidates.append(parents[2] / "docs" / "wpis")

    # 3) docker mount (/app/docs/wpis)
    candidates.append(Path("/app/docs/wpis"))

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # 없더라도 첫 후보를 기준 경로로 사용 (진단 로그를 위해)
    return candidates[0]


WPI_REPORT_ROOT = _resolve_wpi_report_root()

I_TYPE_KR: dict[str, str] = {
    "realist": "리얼리스트",
    "romanticist": "로맨티스트",
    "humanist": "휴머니스트",
    "idealist": "아이디얼리스트",
    "agent": "에이전트",
}

ME_TYPE_KR: dict[str, str] = {
    "relation": "릴레이션",
    "trust": "트러스트",
    "manual": "매뉴얼",
    "self": "셀프",
    "culture": "컬처",
}


@dataclass(frozen=True)
class WpiAutoReport:
    """WPI 조합 유형별 자동 보고서 데이터."""

    i_type: str
    me_type: str
    i_type_kr: str
    me_type_kr: str
    basic_need: str
    strengths: str
    weaknesses: str
    personality_description: str
    me_specific_analysis: str
    me_common_analysis: str | None
    me_context_analysis: str
    full_text: str
    default_block_id: str
    specific_block_id: str
    common_block_id: str | None
    block_ids: tuple[str, ...]


def _normalize_type(value: str) -> str:
    return value.strip().lower()


def _read_text(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8").strip()


def _strip_prefix(line: str) -> str:
    trimmed = line.strip()
    return trimmed.lstrip("▶•\u2022-■*").strip()


def _parse_structured_fields(lines: list[str]) -> tuple[str, str, str, list[str]]:
    """기본욕구/강점/약점 라인을 추출하고, 설명 본문 라인만 남긴다."""

    basic_need = ""
    strengths = ""
    weaknesses = ""
    body_lines: list[str] = []

    for line in lines:
        line = _strip_prefix(line)
        if not line:
            continue

        if line.startswith("기본욕구"):
            basic_need = re.sub(r"^기본욕구\s*: ?", "", line).strip().strip(",")
            continue

        if line.startswith("기본 욕구"):
            basic_need = re.sub(r"^기본 욕구\s*: ?", "", line).strip().strip(",")
            continue

        if line.startswith("강점"):
            strengths = re.sub(r"^강점\s*: ?", "", line).strip().strip(",")
            continue

        if line.startswith("약점"):
            weaknesses = re.sub(r"^약점\s*: ?", "", line).strip().strip(",")
            continue

        body_lines.append(line)

    if not body_lines:
        body_lines = [l.strip() for l in lines]

    return basic_need, strengths, weaknesses, body_lines


def _profile_path(i_type: str, filename: str) -> Path:
    return (WPI_REPORT_ROOT / i_type) / filename


def _load_default_profile(i_type: str) -> tuple[str, str, str, str]:
    default_path = _profile_path(i_type, "default.txt")
    raw = _read_text(default_path)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    basic_need, strengths, weaknesses, body_lines = _parse_structured_fields(lines)
    personality_description = "\n".join(body_lines).strip()
    return basic_need, strengths, weaknesses, personality_description


def _load_me_context_parts(i_type: str, me_type: str) -> tuple[str, str | None]:
    specific_path = _profile_path(i_type, f"{me_type}.txt")
    if not specific_path.exists():
        raise FileNotFoundError(f"Missing combination profile: {specific_path}")

    specific_text = _read_text(specific_path)

    common_path = WPI_REPORT_ROOT / "_common" / f"{me_type}_base.txt"
    common_text: str | None = None
    if common_path.exists():
        maybe_common_text = _read_text(common_path)
        if maybe_common_text:
            common_text = maybe_common_text

    return specific_text, common_text


def _load_me_context(i_type: str, me_type: str) -> str:
    specific_text, common_text = _load_me_context_parts(i_type, me_type)
    segments = [specific_text]
    if common_text:
        segments.append(common_text)

    return "\n\n".join(segment for segment in segments if segment)


def _load_full_text(i_type: str, me_type: str) -> str:
    default_text = _read_text(_profile_path(i_type, "default.txt"))
    specific_text = _read_text(_profile_path(i_type, f"{me_type}.txt"))
    return f"{default_text}\n\n{specific_text}".strip()


def _validate_type(i_type: str, me_type: str) -> tuple[str, str]:
    i_type_key = _normalize_type(i_type)
    me_type_key = _normalize_type(me_type)

    if i_type_key not in {t.lower() for t in I_TEST_TYPES}:
        raise ValueError(f"Invalid I type: {i_type}")
    if me_type_key not in {t.lower() for t in ME_TEST_TYPES}:
        raise ValueError(f"Invalid Me type: {me_type}")

    return i_type_key, me_type_key


@lru_cache(maxsize=1)
def load_all_auto_reports() -> dict[tuple[str, str], WpiAutoReport]:
    """25개 조합 프로필을 한 번만 로드하고 캐시한다."""

    reports: dict[tuple[str, str], WpiAutoReport] = {}
    logger.info(f"WPI report root resolved to: {WPI_REPORT_ROOT}")

    for i_type in I_TEST_TYPES:
        i_type_key = _normalize_type(i_type)
        default_path = _profile_path(i_type_key, "default.txt")
        if not default_path.exists():
            logger.warning(f"WPI default file not found: {default_path}")
            continue

        basic_need, strengths, weaknesses, personality = _load_default_profile(
            i_type_key
        )

        for me_type in ME_TEST_TYPES:
            me_type_key = _normalize_type(me_type)
            specific_path = _profile_path(i_type_key, f"{me_type_key}.txt")
            if not specific_path.exists():
                logger.warning(f"WPI profile file missing: {specific_path}")
                continue

            me_specific, me_common = _load_me_context_parts(i_type_key, me_type_key)
            segments = [me_specific]
            if me_common:
                segments.append(me_common)
            me_context = "\n\n".join(segment for segment in segments if segment)

            full_text = _load_full_text(i_type_key, me_type_key)
            default_block_id = f"base:{i_type_key}:default"
            specific_block_id = f"pair:{i_type_key}:{me_type_key}:specific"
            common_block_id = f"common:{me_type_key}:base" if me_common else None

            block_ids_list = [default_block_id, specific_block_id]
            if common_block_id:
                block_ids_list.append(common_block_id)

            reports[(i_type_key, me_type_key)] = WpiAutoReport(
                i_type=i_type_key,
                me_type=me_type_key,
                i_type_kr=I_TYPE_KR.get(i_type_key, i_type_key),
                me_type_kr=ME_TYPE_KR.get(me_type_key, me_type_key),
                basic_need=basic_need,
                strengths=strengths,
                weaknesses=weaknesses,
                personality_description=personality,
                me_specific_analysis=me_specific,
                me_common_analysis=me_common,
                me_context_analysis=me_context,
                full_text=full_text,
                default_block_id=default_block_id,
                specific_block_id=specific_block_id,
                common_block_id=common_block_id,
                block_ids=tuple(block_ids_list),
            )

    if len(reports) < 25:
        logger.warning(f"Loaded incomplete WPI report set: {len(reports)}/25")

    return reports


def get_auto_report(i_type: str, me_type: str) -> WpiAutoReport | None:
    """조합 유형 키로 보고서 조회."""

    i_type_key, me_type_key = _validate_type(i_type, me_type)
    return load_all_auto_reports().get((i_type_key, me_type_key))
