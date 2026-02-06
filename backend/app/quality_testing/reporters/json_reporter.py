"""
JSON 리포트 생성기

JSON 형식으로 테스트 결과를 저장
"""

import json
from pathlib import Path
from datetime import datetime

from ..core.interfaces import IReporter, TestReport


class JSONReporter(IReporter):
    """JSON 형식 리포트 생성기"""

    def __init__(self, output_dir: str = "./quality_test_reports"):
        """
        Args:
            output_dir: 리포트 저장 디렉토리
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self, report: TestReport) -> str:
        """
        JSON 리포트 생성

        Args:
            report: TestReport 객체

        Returns:
            생성된 리포트 파일 경로
        """
        timestamp = datetime.fromtimestamp(report.timestamp).strftime("%Y%m%d_%H%M%S")
        filename = f"quality_test_{timestamp}.json"
        file_path = self.output_dir / filename

        # JSON 직렬화 (Pydantic model_dump 사용)
        report_dict = report.model_dump()

        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2, ensure_ascii=False)

        return str(file_path)
