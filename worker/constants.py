"""워커 상수 정의.

백엔드와 독립적으로 사용할 수 있는 상태 Enum 등을 정의합니다.
"""
import enum


class FileStatus(str, enum.Enum):
    """파일 처리 상태."""

    # 초기 상태
    QUEUED = "QUEUED"  # 처리 대기중 (큐에 등록됨)
    
    # 진행 중 상태
    PROCESSING = "PROCESSING"  # ASR/화자분리 진행 중
    OCR_PROCESSING = "OCR_PROCESSING"  # OCR 처리 중
    SUMMARY_QUEUED = "SUMMARY_QUEUED"  # LLM 요약 대기중 (큐에 등록됨)
    SUMMARIZING = "SUMMARIZING"  # LLM 요약 중
    
    # 완료 상태
    COMPLETED = "COMPLETED"  # 전체 파이프라인 완료
    
    # 실패 상태
    ASR_FAILED = "ASR_FAILED"  # ASR/화자분리 단계 실패
    OCR_FAILED = "OCR_FAILED"  # OCR 처리 실패
    SUMMARY_FAILED = "SUMMARY_FAILED"  # LLM 요약 실패
    
    # 취소 상태
    CANCELLED = "CANCELLED"  # 취소됨 (사용자 취소 또는 타임아웃)


class ContentType(str, enum.Enum):
    """파일 콘텐츠 타입."""

    AUDIO = "AUDIO"  # 오디오 파일
    DOCUMENT = "DOCUMENT"  # 문서 파일
    PORTRAY = "PORTRAY"  # 이미지 묘사


# 하위 호환성을 위한 별칭
ContentStatus = FileStatus
