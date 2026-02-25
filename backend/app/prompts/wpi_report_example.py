"""WPI 마음 읽기 리포트 프롬프트 예시 파일.

실제 프롬프트는 wpi_report.py에 작성하세요 (로컬 전용, .gitignore 처리됨).
이 파일을 복사하여 wpi_report.py를 생성한 뒤 내용을 채워넣으세요.

  cp backend/app/prompts/wpi_report_example.py backend/app/prompts/wpi_report.py
"""

WPI_REPORT_SYSTEM_PROMPT = """
당신은 WPI 심리 프로파일 상담사입니다.
(실제 프롬프트는 wpi_report.py에 작성하세요)
""".strip()

WPI_REPORT_USER_TEMPLATE = """
다음 WPI 검사 프로파일을 바탕으로 마음 읽기 리포트를 작성하세요.

## 점수 프로파일
{score_profile_json}

## 해석 재료
{collected_texts}

(실제 작성 규칙은 wpi_report.py에 작성하세요)
""".strip()
