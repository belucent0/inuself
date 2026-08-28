"""LangGraph 에이전트 유닛 테스트 공통 설정."""

from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("SIGNUP_ACCESS_CODE", "test-only-signup-access-code")

_backend_path = str(Path(__file__).parents[3] / "backend")
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)
