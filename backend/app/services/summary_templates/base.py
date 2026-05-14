"""요약 템플릿 base 정의.

generic block schema의 핵심 dataclass. template_id별로 block 구성을 달리하여
도메인별 요약 구조(회의록/강의/상담 등)를 확장할 수 있도록 추상화한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class BlockStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


BlockType = Literal["text", "list", "long_text"]


@dataclass(frozen=True)
class BlockDef:
    """정적 block 정의 (template에 포함)."""

    key: str
    label: str
    type: BlockType
    prompt_key: str
    depends_on: tuple[str, ...] = ()
    required: bool = True
    # depends_on 결과로 N개 expand되는 dynamic block 표식 (key='section_*')
    dynamic: bool = False
    # dynamic=True일 때 expand 기준이 되는 block key (예: 'headings')
    expand_from: str | None = None


@dataclass(frozen=True)
class BlockTemplate:
    """요약 템플릿. 코드 정의 (PR-A 한정 — 향후 DB 정의도 가능)."""

    id: str
    name: str
    blocks: tuple[BlockDef, ...]
    # 한 LLM 호출에서 묶음 추출되는 block key 그룹 (예: title/keywords/headings)
    group_extracts: tuple[tuple[str, ...], ...] = field(default_factory=tuple)

    def get_def(self, key: str) -> BlockDef | None:
        for b in self.blocks:
            if b.key == key:
                return b
        return None

    def group_for(self, key: str) -> tuple[str, ...] | None:
        for group in self.group_extracts:
            if key in group:
                return group
        return None
