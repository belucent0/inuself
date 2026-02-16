"""LangGraph 기반 WPI 리포트 생성 실행기."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from worker.config import WorkerSettings
from worker.logging_config import logger
from worker.pipelines.llm.litellm_client import request_litellm_completion

WPI_SECTION_SYSTEM_PROMPT = """
당신은 WPI 심리검사 리포트 섹션 작성 전문가입니다.

규칙:
1) 반드시 한국어로 작성합니다.
2) 제공된 데이터와 근거만 사용합니다.
3) 단정/낙인/진단 표현은 금지합니다.
4) 실행 가능한 문장으로 구체적으로 작성합니다.
5) 요청한 형식을 반드시 지킵니다.
""".strip()


@dataclass(frozen=True)
class SectionSpec:
    """WPI 리포트 섹션 생성 스펙."""

    id: str
    title: str
    min_chars: int
    min_bullets: int
    format_hint: str
    focus_hint: str


SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec(
        id="overview",
        title="종합 해석",
        min_chars=360,
        min_bullets=0,
        format_hint="2~3개 문단으로 작성",
        focus_hint="우세형/보조형, gap_highlights, pair_gap_profile, score_insights를 함께 해석",
    ),
    SectionSpec(
        id="strengths",
        title="핵심 강점",
        min_chars=320,
        min_bullets=4,
        format_hint="'- ' 불릿 4개 이상",
        focus_hint="실무/관계 장점을 auto_profile.strengths와 점수 근거로 연결",
    ),
    SectionSpec(
        id="cautions",
        title="주의 포인트",
        min_chars=320,
        min_bullets=4,
        format_hint="'- ' 불릿 4개 이상",
        focus_hint="pair_gap_profile bucket과 gap이 큰 축, 약한 유형, 과잉 적응 리스크를 구체적으로 서술",
    ),
    SectionSpec(
        id="actions",
        title="상황별 제안",
        min_chars=420,
        min_bullets=6,
        format_hint="'### 개인 실행' / '### 협업/소통' 소제목 + 각 3개 이상 불릿",
        focus_hint="각 제안은 작게 시작할 행동 단위로 작성",
    ),
    SectionSpec(
        id="secondary",
        title="비우세형(보조 프로파일) 해석",
        min_chars=280,
        min_bullets=0,
        format_hint="1~2개 문단으로 작성",
        focus_hint="secondary_profiles와 top_types를 활용해 우세형 바깥 성향을 설명",
    ),
    SectionSpec(
        id="comment",
        title="코멘트",
        min_chars=160,
        min_bullets=0,
        format_hint="2~4문장",
        focus_hint="맥락 의존성과 변화 가능성을 안내",
    ),
)


class WpiReportState(TypedDict):
    """WPI 리포트 LangGraph 상태."""

    context: dict[str, Any]
    features: dict[str, Any]
    section_specs: list[dict[str, Any]]
    sections: dict[str, str]
    validation_errors: dict[str, str]
    attempt: int
    max_retries: int
    final_markdown: str


def _sort_scores(
    scores: dict[str, float], top_k: int = 3
) -> list[dict[str, float | str]]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        {"type": str(type_name), "score": float(score)}
        for type_name, score in ranked[:top_k]
    ]


def _extract_gap_highlights(
    gap_analysis: dict[str, Any],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    axis_gaps = (
        gap_analysis.get("axis_gaps") if isinstance(gap_analysis, dict) else None
    )
    if not isinstance(axis_gaps, dict):
        return []

    highlights: list[dict[str, Any]] = []
    for axis_name, payload in axis_gaps.items():
        if not isinstance(payload, dict):
            continue

        gap_value = float(payload.get("gap", 0.0))
        direction = "균형"
        if gap_value > 0:
            direction = "i_test 우세"
        elif gap_value < 0:
            direction = "me_test 우세"

        highlights.append(
            {
                "axis": str(axis_name),
                "i_type": payload.get("i_type"),
                "me_type": payload.get("me_type"),
                "gap": gap_value,
                "abs_gap": abs(gap_value),
                "direction": direction,
            }
        )

    highlights.sort(key=lambda item: item["abs_gap"], reverse=True)
    return highlights[:top_k]


def _count_bullets(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith("- "))


class WpiReportGraphExecutor:
    """WPI 리포트 상세 생성을 위한 LangGraph 실행기."""

    def __init__(self, settings: WorkerSettings, max_retries: int = 2):
        self.settings = settings
        self.max_retries = max_retries
        self.graph = self._build_graph()

    def _wpi_model_name(self) -> str:
        return (
            self.settings.wpi_report_litellm_model
            or self.settings.litellm_model_summarize
        )

    async def execute(self, context: dict[str, Any]) -> str:
        initial_state: WpiReportState = {
            "context": context,
            "features": {},
            "section_specs": [],
            "sections": {},
            "validation_errors": {},
            "attempt": 0,
            "max_retries": self.max_retries,
            "final_markdown": "",
        }

        result = await self.graph.ainvoke(initial_state)
        report_md = str(result.get("final_markdown", "")).strip()
        if not report_md:
            raise ValueError("WPI report graph produced an empty report")
        return report_md

    def _build_graph(self):
        builder = StateGraph(WpiReportState)

        builder.add_node("plan", self._plan_node)
        builder.add_node("generate", self._generate_sections_node)
        builder.add_node("validate", self._validate_node)
        builder.add_node("retry", self._retry_failed_sections_node)
        builder.add_node("assemble", self._assemble_node)

        builder.set_entry_point("plan")
        builder.add_edge("plan", "generate")
        builder.add_edge("generate", "validate")
        builder.add_conditional_edges(
            "validate",
            self._route_after_validation,
            {"retry": "retry", "assemble": "assemble"},
        )
        builder.add_edge("retry", "validate")
        builder.add_edge("assemble", END)

        return builder.compile()

    def _build_features(self, context: dict[str, Any]) -> dict[str, Any]:
        i_test_obj = context.get("i_test")
        me_test_obj = context.get("me_test")
        if not isinstance(i_test_obj, dict):
            i_test_obj = {}
        if not isinstance(me_test_obj, dict):
            me_test_obj = {}

        i_scores_obj = i_test_obj.get("scores")
        me_scores_obj = me_test_obj.get("scores")
        if not isinstance(i_scores_obj, dict):
            i_scores_obj = {}
        if not isinstance(me_scores_obj, dict):
            me_scores_obj = {}

        i_scores = {str(key): float(value) for key, value in i_scores_obj.items()}
        me_scores = {str(key): float(value) for key, value in me_scores_obj.items()}

        i_ranked = _sort_scores(i_scores)
        me_ranked = _sort_scores(me_scores)

        i_margin = None
        if len(i_ranked) >= 2:
            i_margin = float(i_ranked[0]["score"]) - float(i_ranked[1]["score"])

        me_margin = None
        if len(me_ranked) >= 2:
            me_margin = float(me_ranked[0]["score"]) - float(me_ranked[1]["score"])

        return {
            "dominant_i": i_test_obj.get("dominant_type"),
            "dominant_me": me_test_obj.get("dominant_type"),
            "i_top_types": i_ranked,
            "me_top_types": me_ranked,
            "dominant_margin": {
                "i_test": i_margin,
                "me_test": me_margin,
            },
            "gap_highlights": _extract_gap_highlights(context.get("gap_analysis", {})),
            "pair_gap_profile": context.get("pair_gap_profile"),
            "pair_gap_block": context.get("pair_gap_block"),
            "routing": context.get("routing"),
            "auto_profile": context.get("auto_profile"),
            "secondary_profiles": context.get("secondary_profiles", []),
            "selected_block_ids": context.get("selected_block_ids", []),
        }

    def _plan_node(self, state: WpiReportState) -> WpiReportState:
        features = self._build_features(state["context"])
        section_specs = [
            {
                "id": spec.id,
                "title": spec.title,
                "min_chars": spec.min_chars,
                "min_bullets": spec.min_bullets,
                "format_hint": spec.format_hint,
                "focus_hint": spec.focus_hint,
            }
            for spec in SECTION_SPECS
        ]

        logger.info(
            "[WPI Graph] plan built: dominant_i=%s dominant_me=%s sections=%d",
            features.get("dominant_i"),
            features.get("dominant_me"),
            len(section_specs),
        )

        return {
            **state,
            "features": features,
            "section_specs": section_specs,
        }

    async def _generate_section_content(
        self,
        spec: dict[str, Any],
        context: dict[str, Any],
        features: dict[str, Any],
        feedback: str | None = None,
    ) -> str:
        feedback_block = ""
        if feedback:
            feedback_block = (
                "\n[이전 검증 피드백]\n"
                f"{feedback}\n"
                "위 피드백을 반드시 해결하여 다시 작성하세요.\n"
            )

        prompt = (
            "다음 데이터를 근거로 지정된 섹션 본문만 작성하세요.\n\n"
            "[리포트 입력 데이터]\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "[해석 보조 피처]\n"
            f"{json.dumps(features, ensure_ascii=False, indent=2)}\n\n"
            "[섹션 스펙]\n"
            f"- 제목: {spec['title']}\n"
            f"- 형식: {spec['format_hint']}\n"
            f"- 최소 길이: {spec['min_chars']}자\n"
            f"- 최소 불릿 수: {spec['min_bullets']}\n"
            f"- 핵심 초점: {spec['focus_hint']}\n"
            "- 근거 규칙: 점수 또는 gap 축 근거를 최소 2개 이상 문장에 반영\n"
            "- 금지: 진단 단정, 근거 없는 추측, 과장 표현\n"
            "- 출력: 섹션 제목(##) 없이 본문만 출력\n"
            f"{feedback_block}"
        )

        messages = [
            {"role": "system", "content": WPI_SECTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        llm_call = partial(
            request_litellm_completion,
            settings=self.settings,
            messages=messages,
            model=self._wpi_model_name(),
            request_timeout_seconds=self.settings.wpi_report_llm_request_timeout_seconds,
            max_retry_time=self.settings.wpi_report_llm_busy_max_seconds,
            retry_interval=self.settings.wpi_report_llm_retry_interval_seconds,
        )
        section_timeout_seconds = max(
            int(
                self.settings.wpi_report_llm_request_timeout_seconds
                + self.settings.wpi_report_llm_busy_max_seconds
                + 10
            ),
            30,
        )
        response = await asyncio.wait_for(
            asyncio.to_thread(llm_call),
            timeout=section_timeout_seconds,
        )
        return str(response).strip()

    async def _generate_sections(
        self,
        state: WpiReportState,
        target_specs: list[dict[str, Any]],
        feedbacks: dict[str, str] | None = None,
    ) -> dict[str, str]:
        if not target_specs:
            return dict(state["sections"])

        feedbacks = feedbacks or {}
        sections = dict(state["sections"])

        for spec in target_specs:
            section_id = spec["id"]
            try:
                result = await self._generate_section_content(
                    spec=spec,
                    context=state["context"],
                    features=state["features"],
                    feedback=feedbacks.get(section_id),
                )
            except Exception as exc:
                logger.error(
                    "[WPI Graph] section generation failed: %s - %s",
                    section_id,
                    exc,
                )
                continue
            sections[section_id] = str(result)

        return sections

    async def _generate_sections_node(self, state: WpiReportState) -> WpiReportState:
        sections = await self._generate_sections(
            state=state,
            target_specs=state["section_specs"],
            feedbacks=None,
        )
        return {
            **state,
            "sections": sections,
            "validation_errors": {},
        }

    def _validate_actions_section(self, content: str) -> str | None:
        lines = content.splitlines()
        current = None
        personal_count = 0
        collaboration_count = 0

        for raw_line in lines:
            line = raw_line.strip()
            if line == "### 개인 실행":
                current = "personal"
                continue
            if line == "### 협업/소통":
                current = "collaboration"
                continue
            if line.startswith("- "):
                if current == "personal":
                    personal_count += 1
                elif current == "collaboration":
                    collaboration_count += 1

        if personal_count < 3 or collaboration_count < 3:
            return (
                "'### 개인 실행'과 '### 협업/소통' 소제목 아래에 각각 3개 이상 불릿이 필요합니다. "
                f"(개인={personal_count}, 협업={collaboration_count})"
            )
        return None

    def _validate_node(self, state: WpiReportState) -> WpiReportState:
        errors: dict[str, str] = {}

        for spec in state["section_specs"]:
            section_id = spec["id"]
            content = str(state["sections"].get(section_id, "")).strip()
            if not content:
                errors[section_id] = "섹션 내용이 비어 있습니다"
                continue

            min_chars = int(spec.get("min_chars", 0))
            if len(content) < min_chars:
                errors[section_id] = (
                    f"최소 길이 미달: {len(content)}자 / 요구 {min_chars}자"
                )
                continue

            min_bullets = int(spec.get("min_bullets", 0))
            if min_bullets > 0:
                bullet_count = _count_bullets(content)
                if bullet_count < min_bullets:
                    errors[section_id] = (
                        f"불릿 수 부족: {bullet_count}개 / 요구 {min_bullets}개"
                    )
                    continue

            if section_id == "actions":
                action_error = self._validate_actions_section(content)
                if action_error:
                    errors[section_id] = action_error

        logger.info(
            "[WPI Graph] validation: attempt=%d, invalid_sections=%d",
            state["attempt"],
            len(errors),
        )

        return {
            **state,
            "validation_errors": errors,
        }

    def _route_after_validation(
        self, state: WpiReportState
    ) -> Literal["retry", "assemble"]:
        if state["validation_errors"] and state["attempt"] < state["max_retries"]:
            return "retry"
        return "assemble"

    async def _retry_failed_sections_node(
        self, state: WpiReportState
    ) -> WpiReportState:
        failed_ids = set(state["validation_errors"].keys())
        target_specs = [
            spec for spec in state["section_specs"] if spec["id"] in failed_ids
        ]

        logger.info(
            "[WPI Graph] retrying sections: attempt=%d, targets=%s",
            state["attempt"] + 1,
            sorted(failed_ids),
        )

        sections = await self._generate_sections(
            state=state,
            target_specs=target_specs,
            feedbacks=state["validation_errors"],
        )

        return {
            **state,
            "sections": sections,
            "attempt": state["attempt"] + 1,
        }

    def _assemble_node(self, state: WpiReportState) -> WpiReportState:
        parts: list[str] = []

        for index, spec in enumerate(state["section_specs"], start=1):
            title = spec["title"]
            body = str(state["sections"].get(spec["id"], "")).strip()
            if not body:
                body = "해당 섹션 생성에 실패했습니다."

            parts.append(f"## {index}. {title}")
            parts.append(body)
            parts.append("")

        markdown = "\n".join(parts).strip()
        return {
            **state,
            "final_markdown": markdown,
        }
