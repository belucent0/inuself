"""Worker processors package.

이 패키지는 각 태스크 유형별 처리 로직을 포함합니다.
- asr_processor: ASR (음성 인식) 처리
- llm_processor: LLM (요약) 처리
- ocr_processor: OCR (문서 인식) 처리

NOTE: 상단에서 자동 import하지 않습니다.
각 태스크 모듈에서 필요한 프로세서를 직접 import하세요.
예: from worker.processors.asr_processor import process_transcription_job
"""

# Lazy import를 위해 상단 import 제거
# 필요 시 다음과 같이 사용:
# from worker.processors.asr_processor import process_transcription_job
# from worker.processors.llm_processor import process_llm_job
# from worker.processors.ocr_processor import process_ocr_job

__all__ = [
    "asr_processor",
    "llm_processor",
    "ocr_processor",
]
