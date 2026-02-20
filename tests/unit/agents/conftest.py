"""LangGraph 에이전트 유닛 테스트 공통 설정.

kiwipiepy(한국어 형태소 분석기)는 무거운 NLP 패키지이므로
CI 환경에서는 sys.modules 패치로 대체합니다.
이 파일은 pytest가 테스트 수집 전에 import하므로
모듈 레벨에서 패치가 적용됩니다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


def _mock_if_missing(module: str, *submodules: str) -> None:
    """패키지가 설치되지 않은 경우에만 MagicMock으로 대체합니다 (CI 대응)."""
    if importlib.util.find_spec(module) is None:
        sys.modules[module] = MagicMock()
        for sub in submodules:
            sys.modules[sub] = MagicMock()


# ── 1. 무거운 패키지 mock (설치되지 않은 경우에만) ─────────────────────────────
# backend 모듈을 import하기 전에 sys.modules에 등록해야 합니다.

# kiwipiepy (한국어 형태소 분석기)
_mock_if_missing("kiwipiepy", "kiwipiepy.Kiwi")

# redis (web_search.py가 top-level에서 import)
_mock_if_missing("redis", "redis.asyncio")

# sqlalchemy (rag_search.py → db/models.py, db/session.py가 top-level에서 import)
_mock_if_missing(
    "sqlalchemy",
    "sqlalchemy.orm",
    "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio",
    "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "sqlalchemy.dialects.postgresql.base",
)

# pgvector (db/models.py가 top-level에서 import)
_mock_if_missing("pgvector", "pgvector.sqlalchemy")

# ── 2. 모듈 레벨 부작용이 있는 내부 모듈 mock ──────────────────────────────────
# app.db.session은 import 시 get_settings() 호출 → pydantic_settings + .env 필요
# backend path 추가 전에 등록해야 실제 파일이 로드되지 않습니다.
for _mod in ["app.db.session", "app.db.base", "app.core.config"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# ── 3. backend를 Python path에 추가 ────────────────────────────────────────────
_backend_path = str(Path(__file__).parents[3] / "backend")
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)
