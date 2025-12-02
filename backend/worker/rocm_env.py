"""ROCm 전용 파이썬 패키지를 워커 런타임에 포함시킵니다."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _candidate_roots() -> list[Path]:
    """ROCm 가상환경 루트 후보 경로 리스트."""
    env_path = os.getenv("ROCM_ENV_PATH")
    if env_path:
        return [Path(env_path)]

    project_root = Path(__file__).resolve().parents[2]
    default_path = project_root / "rocm_env"
    return [default_path]


def _site_packages_from_root(root: Path) -> list[Path]:
    """주어진 루트에서 가능한 site-packages 경로를 반환."""
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.append(root / "Lib" / "site-packages")
    else:
        candidates.append(root / "lib" / "python3.12" / "site-packages")
        candidates.append(root / "lib" / "python3.11" / "site-packages")
        candidates.append(root / "lib" / "python3.10" / "site-packages")
    # 사용자가 직접 site-packages 경로를 지정했을 수도 있으므로 루트 자체도 마지막에 추가
    candidates.append(root)
    return candidates


def _resolve_rocm_site_packages() -> Path | None:
    # 가장 우선순위는 명시적인 환경변수
    explicit = os.getenv("ROCM_SITE_PACKAGES")
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.exists():
            return explicit_path

    for root in _candidate_roots():
        if not root.exists():
            continue
        for candidate in _site_packages_from_root(root):
            if candidate.exists():
                return candidate
    return None


def ensure_rocm_on_sys_path() -> None:
    """ROCm site-packages 경로를 sys.path 맨 앞에 추가."""
    rocm_site = _resolve_rocm_site_packages()
    if not rocm_site:
        return
    rocm_site_str = str(rocm_site)
    if rocm_site_str not in sys.path:
        sys.path.insert(0, rocm_site_str)


# 모듈 import 시 즉시 적용
ensure_rocm_on_sys_path()

















