"""LangGraph 에이전트 유닛 테스트 공통 설정.

kiwipiepy(한국어 형태소 분석기)는 무거운 NLP 패키지이므로
CI 환경에서는 sys.modules 패치로 대체합니다.
이 파일은 pytest가 테스트 수집 전에 import하므로
모듈 레벨에서 패치가 적용됩니다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# ── 1. kiwipiepy mock ──────────────────────────────────────────────────────────
# intent_parser.py가 top-level에서 `from kiwipiepy import Kiwi`를 사용하므로
# backend 모듈을 import하기 전에 sys.modules에 등록해야 합니다.
if "kiwipiepy" not in sys.modules:
    _kiwi_mock = MagicMock()
    _kiwi_mock.Kiwi = MagicMock()
    sys.modules["kiwipiepy"] = _kiwi_mock
    sys.modules["kiwipiepy.Kiwi"] = MagicMock()

# ── 2. backend를 Python path에 추가 ────────────────────────────────────────────
_backend_path = str(Path(__file__).parents[3] / "backend")
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)
