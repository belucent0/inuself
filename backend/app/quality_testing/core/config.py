"""
품질 테스트 설정 클래스

Pydantic을 사용한 타입 안전한 설정 관리
"""

from pydantic import BaseModel, Field
from typing import Optional, List


class QualityTestConfig(BaseModel):
    """품질 테스트 설정"""

    # 테스트 범위
    max_conversations: Optional[int] = Field(
        default=None,
        description="테스트할 최대 대화 수 (None이면 전체)",
        ge=1
    )

    # 평가자 설정
    evaluators_enabled: List[str] = Field(
        default=["intent", "search", "citation", "quality"],
        description="활성화할 평가자 목록"
    )

    # 마스킹 설정 (Phase 2+)
    masking_enabled: bool = Field(
        default=False,
        description="마스킹 활성화 여부"
    )

    # 리포트 설정
    report_formats: List[str] = Field(
        default=["json", "markdown"],
        description="생성할 리포트 형식"
    )

    report_output_dir: str = Field(
        default="./quality_test_reports",
        description="리포트 저장 디렉토리"
    )

    # Redis 설정
    redis_host: str = Field(default="localhost", description="Redis 호스트")
    redis_port: int = Field(default=6379, ge=1, le=65535, description="Redis 포트")
    redis_db: int = Field(default=0, ge=0, le=15, description="Redis DB 번호")

    # 기타
    verbose: bool = Field(default=True, description="상세 로그 출력")

    class Config:
        json_schema_extra = {
            "example": {
                "max_conversations": 3,
                "evaluators_enabled": ["intent", "search", "citation", "quality"],
                "masking_enabled": False,
                "report_formats": ["json", "markdown"],
                "report_output_dir": "./quality_test_reports",
                "redis_host": "localhost",
                "redis_port": 6379,
                "redis_db": 0,
                "verbose": True
            }
        }
