"""Content/File 상태 머신.

파일 처리 파이프라인의 상태 전이를 관리합니다.

상태 흐름:
    QUEUED → PULLING/PROCESSING/OCR_PROCESSING
    PULLING → PROCESSING
    PROCESSING → SUMMARY_QUEUED
    OCR_PROCESSING → SUMMARY_QUEUED
    SUMMARY_QUEUED → SUMMARIZING
    SUMMARIZING → COMPLETED

    모든 진행 상태에서 → CANCELLED 가능
    각 단계에서 실패 시 → *_FAILED 상태로 전이
"""

from app.db.models import FileStatus
from app.state_machines.base import BaseStateMachine, StateInfo, TransitionContext


class ContentStateMachine(BaseStateMachine[FileStatus]):
    """File/Content 상태 머신.

    파일 업로드부터 요약 완료까지의 전체 파이프라인을 관리합니다.
    """

    # 상태 전이 규칙 정의
    TRANSITIONS: dict[FileStatus, list[FileStatus]] = {
        # 초기 상태
        FileStatus.QUEUED: [
            FileStatus.PULLING,  # YouTube 등 외부 소스
            FileStatus.PROCESSING,  # 로컬 파일 ASR
            FileStatus.OCR_PROCESSING,  # 문서 OCR
            FileStatus.ASR_FAILED,  # 즉시 실패 대응
            FileStatus.OCR_FAILED,  # 즉시 실패 대응
            FileStatus.CANCELLED,
        ],
        # 진행 중 상태
        FileStatus.PULLING: [
            FileStatus.QUEUED,  # 다운로드 완료 → ASR 대기 (YouTube)
            FileStatus.PROCESSING,  # 다운로드 완료 → ASR 시작
            FileStatus.DOWNLOAD_FAILED,  # 다운로드 실패
            FileStatus.ASR_FAILED,  # 다운로드 실패 (하위 호환)
            FileStatus.CANCELLED,
        ],
        FileStatus.PROCESSING: [
            FileStatus.SUMMARY_QUEUED,  # ASR 완료 → 요약 대기
            FileStatus.ASR_FAILED,  # ASR 실패 (텍스트 없음 포함)
            FileStatus.CANCELLED,
        ],
        FileStatus.OCR_PROCESSING: [
            FileStatus.SUMMARY_QUEUED,  # OCR 완료 → 요약 대기
            FileStatus.OCR_FAILED,  # OCR 실패
            FileStatus.CANCELLED,
        ],
        FileStatus.SUMMARY_QUEUED: [
            FileStatus.SUMMARIZING,  # 요약 시작
            FileStatus.COMPLETED,  # 텍스트 없음 → 요약 건너뛰고 완료
            FileStatus.SUMMARY_FAILED,  # 요약 큐잉 실패
            FileStatus.CANCELLED,
        ],
        FileStatus.SUMMARIZING: [
            FileStatus.COMPLETED,  # 요약 완료
            FileStatus.SUMMARY_FAILED,  # 요약 실패
            FileStatus.CANCELLED,
        ],
        # 터미널 상태 (전이 불가)
        FileStatus.COMPLETED: [],
        FileStatus.DOWNLOAD_FAILED: [],
        FileStatus.ASR_FAILED: [],
        FileStatus.OCR_FAILED: [],
        FileStatus.SUMMARY_FAILED: [],
        FileStatus.CANCELLED: [],
    }

    # 상태별 타임아웃 (분)
    TIMEOUTS: dict[FileStatus, int] = {
        FileStatus.QUEUED: 30,  # 30분 대기 후 stuck
        FileStatus.PULLING: 30,  # 다운로드 30분
        FileStatus.PROCESSING: 60,  # ASR 60분 (긴 오디오)
        FileStatus.OCR_PROCESSING: 30,  # OCR 30분
        FileStatus.SUMMARY_QUEUED: 30,  # 요약 대기 30분
        FileStatus.SUMMARIZING: 35,  # LLM 요약 — BlockGenerator partial retry cap 30분 + 여유 5분
    }

    # 상태별 메타정보
    STATE_INFO: dict[FileStatus, StateInfo] = {
        FileStatus.QUEUED: StateInfo(
            name="대기중",
            description="처리 대기 중",
            is_terminal=False,
            timeout_minutes=30,
            retryable=True,
        ),
        FileStatus.PULLING: StateInfo(
            name="다운로딩",
            description="외부 소스에서 다운로드 중",
            is_terminal=False,
            timeout_minutes=30,
            retryable=True,
        ),
        FileStatus.PROCESSING: StateInfo(
            name="처리중",
            description="ASR/화자분리 처리 중",
            is_terminal=False,
            timeout_minutes=60,
            retryable=False,  # ASR은 외부 서버 의존
        ),
        FileStatus.OCR_PROCESSING: StateInfo(
            name="OCR처리중",
            description="OCR 처리 중",
            is_terminal=False,
            timeout_minutes=30,
            retryable=False,
        ),
        FileStatus.SUMMARY_QUEUED: StateInfo(
            name="요약대기",
            description="LLM 요약 대기 중",
            is_terminal=False,
            timeout_minutes=30,
            retryable=True,
        ),
        FileStatus.SUMMARIZING: StateInfo(
            name="요약중",
            description="LLM 요약 처리 중",
            is_terminal=False,
            timeout_minutes=35,
            retryable=True,
        ),
        FileStatus.COMPLETED: StateInfo(
            name="완료",
            description="처리 완료",
            is_terminal=True,
        ),
        FileStatus.DOWNLOAD_FAILED: StateInfo(
            name="다운로드실패",
            description="외부 소스 다운로드 실패",
            is_terminal=True,
        ),
        FileStatus.ASR_FAILED: StateInfo(
            name="ASR실패",
            description="ASR 처리 실패",
            is_terminal=True,
        ),
        FileStatus.OCR_FAILED: StateInfo(
            name="OCR실패",
            description="OCR 처리 실패",
            is_terminal=True,
        ),
        FileStatus.SUMMARY_FAILED: StateInfo(
            name="요약실패",
            description="LLM 요약 실패",
            is_terminal=True,
        ),
        FileStatus.CANCELLED: StateInfo(
            name="취소됨",
            description="사용자 취소 또는 타임아웃",
            is_terminal=True,
        ),
    }

    # 재시도 가능한 실패 상태 → 재시도 시작 상태 매핑
    RETRY_MAPPING: dict[FileStatus, FileStatus] = {
        FileStatus.DOWNLOAD_FAILED: FileStatus.PULLING,
        FileStatus.ASR_FAILED: FileStatus.QUEUED,
        FileStatus.OCR_FAILED: FileStatus.QUEUED,
        FileStatus.SUMMARY_FAILED: FileStatus.SUMMARY_QUEUED,
    }

    def is_failed_state(self, state: FileStatus) -> bool:
        """실패 상태인지 확인."""
        return state in (
            FileStatus.DOWNLOAD_FAILED,
            FileStatus.ASR_FAILED,
            FileStatus.OCR_FAILED,
            FileStatus.SUMMARY_FAILED,
        )

    def is_processing_state(self, state: FileStatus) -> bool:
        """처리 중 상태인지 확인 (stuck 감지 대상)."""
        return state in (
            FileStatus.QUEUED,
            FileStatus.PULLING,
            FileStatus.PROCESSING,
            FileStatus.OCR_PROCESSING,
            FileStatus.SUMMARY_QUEUED,
            FileStatus.SUMMARIZING,
        )

    def get_retry_target(self, failed_state: FileStatus) -> FileStatus | None:
        """실패 상태에서 재시도 시 시작할 상태 반환."""
        return self.RETRY_MAPPING.get(failed_state)

    def get_failure_state_for(self, processing_state: FileStatus) -> FileStatus | None:
        """처리 상태에 대응하는 실패 상태 반환."""
        mapping = {
            FileStatus.QUEUED: FileStatus.ASR_FAILED,
            FileStatus.PULLING: FileStatus.DOWNLOAD_FAILED,
            FileStatus.PROCESSING: FileStatus.ASR_FAILED,
            FileStatus.OCR_PROCESSING: FileStatus.OCR_FAILED,
            FileStatus.SUMMARY_QUEUED: FileStatus.SUMMARY_FAILED,
            FileStatus.SUMMARIZING: FileStatus.SUMMARY_FAILED,
        }
        return mapping.get(processing_state)


# 싱글톤 인스턴스 (전역에서 사용)
content_state_machine = ContentStateMachine()
