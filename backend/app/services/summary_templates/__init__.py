"""요약 템플릿 registry.

template_id로 template 인스턴스를 조회한다. 등록은 코드 import 시점에 수행.
"""

from .base import BlockDef, BlockStatus, BlockTemplate, BlockType
from .default import DEFAULT_TEMPLATE


_REGISTRY: dict[str, BlockTemplate] = {
    DEFAULT_TEMPLATE.id: DEFAULT_TEMPLATE,
}


def get_template(template_id: str = "default") -> BlockTemplate:
    if template_id not in _REGISTRY:
        raise KeyError(f"Unknown template_id: {template_id}")
    return _REGISTRY[template_id]


def register_template(template: BlockTemplate) -> None:
    """확장용. 도메인 템플릿(회의록/강의 등) 추가 시 사용."""
    _REGISTRY[template.id] = template


__all__ = [
    "BlockDef",
    "BlockStatus",
    "BlockTemplate",
    "BlockType",
    "DEFAULT_TEMPLATE",
    "get_template",
    "register_template",
]
