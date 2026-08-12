"""default 요약 템플릿.

현재(PR-A 이전) 사용 중인 출력 구조와 동일한 결과를 내도록 정의.
- 키워드 / 목차 / 핵심 요약 / 상세 내용 (## h2 단위)
- 본문은 headings 항목별 ### h3

core_summary는 LLM 호출이 아닌 sections 첫 문장 가공 결과이므로
block으로 저장하지 않고 renderer가 sections로부터 직접 생성한다.
"""

from .base import BlockDef, BlockTemplate


DEFAULT_TEMPLATE = BlockTemplate(
    id="default",
    name="기본 (콘텐츠 요약)",
    blocks=(
        BlockDef(
            key="title",
            label="제목",
            type="text",
            prompt_key="phase1_metadata",
        ),
        BlockDef(
            key="keywords",
            label="키워드",
            type="list",
            prompt_key="phase1_metadata",
        ),
        BlockDef(
            key="headings",
            label="목차",
            type="list",
            prompt_key="phase1_metadata",
        ),
        BlockDef(
            key="section_*",
            label="(from headings)",
            type="long_text",
            prompt_key="section_body",
            depends_on=("headings",),
            dynamic=True,
            expand_from="headings",
        ),
    ),
    group_extracts=(
        ("title", "keywords", "headings"),
    ),
)
