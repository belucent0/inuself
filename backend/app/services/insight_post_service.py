"""영상 기반 인사이트 글 생성/관리 서비스."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..agents.tools.web_search import WebSearchError, search_web
from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import get_secure_media_url
from ..db import models
from ..repositories.file_repository import FileRepository
from ..schemas.insight import (
    InsightAnnotationSchema,
    InsightEvidenceSchema,
    InsightPostCreateRequest,
    InsightPostDetail,
    InsightPostListItem,
    InsightPostListResponse,
    InsightPostSource,
    InsightPostUpdateRequest,
    InsightResearchRequest,
)
from .ai_gateway_client import AIGatewayClientError, request_ai_gateway_completion_async


MAX_SUMMARY_CHARS = 4000
TRANSCRIPT_CHARS_BY_TARGET_LENGTH = {
    "short": 3000,
    "medium": 5000,
    "long": 8000,
}
OUTPUT_TOKENS_BY_TARGET_LENGTH = {
    "short": 900,
    "medium": 1300,
    "long": 1800,
}


class InsightPostService:
    """Content/ASR 결과를 읽어 인사이트 블로그 글을 생성한다."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.settings = get_settings()

    async def list_posts(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 12,
    ) -> InsightPostListResponse:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        offset = (page - 1) * page_size

        total_stmt = select(func.count(models.InsightPost.id)).where(
            models.InsightPost.user_id == user_id
        )
        total = (await self.session.execute(total_stmt)).scalar_one() or 0

        stmt = (
            select(models.InsightPost)
            .options(
                selectinload(models.InsightPost.source_file).selectinload(models.File.content),
                selectinload(models.InsightPost.evidences),
            )
            .where(models.InsightPost.user_id == user_id)
            .order_by(models.InsightPost.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        return InsightPostListResponse(
            items=[self._to_list_item(post) for post in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if total else 0,
        )

    async def get_post(self, post_id: UUID, *, user_id: UUID) -> InsightPostDetail:
        post = await self._get_post_model(post_id, user_id=user_id)
        if not post:
            raise ValueError("인사이트 글을 찾을 수 없습니다")
        return self._to_detail(post)

    async def create_from_content(
        self,
        content_id: UUID,
        *,
        user_id: UUID,
        request: InsightPostCreateRequest,
    ) -> InsightPostDetail:
        source = await self.file_repo.get_file(content_id)
        if not source or not source.content:
            raise ValueError("콘텐츠를 찾을 수 없습니다")
        if source.content.user_id != user_id:
            raise ValueError("해당 콘텐츠에 대한 권한이 없습니다")

        transcript_data = (
            source.content.transcription_result.transcription
            if source.content.transcription_result
            else {}
        )
        transcript_text = _extract_transcript_text(transcript_data)
        summary_md = source.content.summary_md or ""

        if not transcript_text and not summary_md:
            raise ValueError("전사 또는 요약이 있는 콘텐츠만 인사이트 글로 만들 수 있습니다")

        generation = await self._generate_post_payload(
            source=source,
            summary_md=summary_md,
            transcript_text=transcript_text,
            request=request,
        )

        now = datetime.now(timezone.utc)
        post = models.InsightPost(
            user_id=user_id,
            source_file_id=source.id,
            title=generation["title"],
            subtitle=generation.get("subtitle"),
            body_md=generation["body_md"],
            post_type=request.post_type,
            tone=request.tone,
            status="draft",
            metadata_={
                "target_length": request.target_length,
                "generation_mode": generation.get("generation_mode", "unknown"),
                "generation_input": generation.get("generation_input", {}),
                "research_queries": generation.get("research_queries", []),
                "source_title": source.content.title or source.filename,
            },
            created_at=now,
            updated_at=now,
        )
        self.session.add(post)
        await self.session.flush()

        for quote in generation.get("quotes", [])[:8]:
            quote_text = str(quote.get("text") or "").strip()
            if not quote_text:
                continue
            self.session.add(
                models.InsightEvidence(
                    post_id=post.id,
                    source_type="video",
                    title=f"{source.content.title or source.filename} 발언",
                    url=source.source_url,
                    quote_text=quote_text,
                    timestamp_seconds=_coerce_float(quote.get("timestamp_seconds")),
                    metadata_={
                        "speaker": quote.get("speaker"),
                    },
                    created_at=now,
                )
            )

        post_id = post.id
        await self.session.commit()
        self.session.expire_all()
        return await self.get_post(post_id, user_id=user_id)

    async def create_generation_job_from_content(
        self,
        content_id: UUID,
        *,
        user_id: UUID,
        request: InsightPostCreateRequest,
    ) -> InsightPostDetail:
        source = await self.file_repo.get_file(content_id)
        if not source or not source.content:
            raise ValueError("콘텐츠를 찾을 수 없습니다")
        if source.content.user_id != user_id:
            raise ValueError("해당 콘텐츠에 대한 권한이 없습니다")

        transcript_data = (
            source.content.transcription_result.transcription
            if source.content.transcription_result
            else {}
        )
        transcript_text = _extract_transcript_text(transcript_data)
        summary_md = source.content.summary_md or ""
        if not transcript_text and not summary_md:
            raise ValueError("전사 또는 요약이 있는 콘텐츠만 인사이트 글로 만들 수 있습니다")

        now = datetime.now(timezone.utc)
        source_title = source.content.title or source.filename
        post = models.InsightPost(
            user_id=user_id,
            source_file_id=source.id,
            title=source_title,
            subtitle="영상 기반 인사이트 글을 생성하고 있습니다.",
            body_md="## 생성 중\n\n전사와 요약을 바탕으로 인사이트 글을 작성하고 있습니다.",
            post_type=request.post_type,
            tone=request.tone,
            status="generating",
            metadata_={
                "target_length": request.target_length,
                "generation_mode": "pending",
                "generation_started_at": now.isoformat(),
                "source_title": source_title,
            },
            created_at=now,
            updated_at=now,
        )
        self.session.add(post)
        await self.session.flush()

        post_id = post.id
        await self.session.commit()
        self.session.expire_all()
        return await self.get_post(post_id, user_id=user_id)

    async def generate_pending_post(
        self,
        post_id: UUID,
        *,
        user_id: UUID,
        request: InsightPostCreateRequest,
    ) -> InsightPostDetail | None:
        post = await self._get_post_model(post_id, user_id=user_id)
        if not post:
            return None
        if not post.source_file or not post.source_file.content:
            await self._mark_generation_failed(
                post_id,
                user_id=user_id,
                error="원본 콘텐츠를 찾을 수 없습니다",
            )
            return None

        source = post.source_file
        content = source.content
        transcript_data = (
            content.transcription_result.transcription
            if content.transcription_result
            else {}
        )
        transcript_text = _extract_transcript_text(transcript_data)
        summary_md = content.summary_md or ""

        try:
            generation = await self._generate_post_payload(
                source=source,
                summary_md=summary_md,
                transcript_text=transcript_text,
                request=request,
            )
        except Exception as exc:
            await self.session.rollback()
            await self._mark_generation_failed(post_id, user_id=user_id, error=str(exc))
            raise

        now = datetime.now(timezone.utc)
        post.title = generation["title"]
        post.subtitle = generation.get("subtitle")
        post.body_md = generation["body_md"]
        post.post_type = request.post_type
        post.tone = request.tone
        post.status = "draft"
        post.metadata_ = {
            **(post.metadata_ or {}),
            "target_length": request.target_length,
            "generation_mode": generation.get("generation_mode", "unknown"),
            "generation_input": generation.get("generation_input", {}),
            "generation_completed_at": now.isoformat(),
            "research_queries": generation.get("research_queries", []),
            "source_title": content.title or source.filename,
        }
        post.updated_at = now

        for quote in generation.get("quotes", [])[:8]:
            quote_text = str(quote.get("text") or "").strip()
            if not quote_text:
                continue
            self.session.add(
                models.InsightEvidence(
                    post_id=post.id,
                    source_type="video",
                    title=f"{content.title or source.filename} 발언",
                    url=source.source_url,
                    quote_text=quote_text,
                    timestamp_seconds=_coerce_float(quote.get("timestamp_seconds")),
                    metadata_={"speaker": quote.get("speaker")},
                    created_at=now,
                )
            )

        await self.session.commit()
        self.session.expire_all()
        return await self.get_post(post_id, user_id=user_id)

    async def restart_generation(
        self,
        post_id: UUID,
        *,
        user_id: UUID,
        request: InsightPostCreateRequest,
    ) -> InsightPostDetail:
        post = await self._get_post_model(post_id, user_id=user_id)
        if not post:
            raise ValueError("인사이트 글을 찾을 수 없습니다")
        if post.status == "generating":
            raise ValueError("이미 글을 생성 중입니다")
        if post.status != "failed":
            raise ValueError("생성에 실패한 글만 다시 생성할 수 있습니다")
        if not post.source_file or not post.source_file.content:
            raise ValueError("원본 콘텐츠를 찾을 수 없습니다")

        content = post.source_file.content
        transcript_data = (
            content.transcription_result.transcription
            if content.transcription_result
            else {}
        )
        transcript_text = _extract_transcript_text(transcript_data)
        summary_md = content.summary_md or ""
        if not transcript_text and not summary_md:
            raise ValueError("전사 또는 요약이 있는 콘텐츠만 인사이트 글로 만들 수 있습니다")

        now = datetime.now(timezone.utc)
        source_title = content.title or post.source_file.filename
        metadata = {
            **(post.metadata_ or {}),
            "target_length": request.target_length,
            "generation_mode": "pending",
            "generation_started_at": now.isoformat(),
            "source_title": source_title,
        }
        metadata.pop("generation_error", None)
        metadata.pop("generation_failed_at", None)
        metadata.pop("generation_completed_at", None)

        post.title = source_title
        post.subtitle = "영상 기반 인사이트 글을 다시 생성하고 있습니다."
        post.body_md = "## 생성 중\n\n전사와 요약을 바탕으로 인사이트 글을 다시 작성하고 있습니다."
        post.post_type = request.post_type
        post.tone = request.tone
        post.status = "generating"
        post.metadata_ = metadata
        post.updated_at = now

        await self.session.commit()
        self.session.expire_all()
        return await self.get_post(post_id, user_id=user_id)

    async def _mark_generation_failed(
        self,
        post_id: UUID,
        *,
        user_id: UUID,
        error: str,
    ) -> None:
        post = await self._get_post_model(post_id, user_id=user_id)
        if not post:
            return

        now = datetime.now(timezone.utc)
        post.status = "failed"
        post.subtitle = "글 생성에 실패했습니다."
        post.body_md = "## 생성 실패\n\nAI Gateway 상태를 확인한 뒤 다시 생성해 주세요."
        post.metadata_ = {
            **(post.metadata_ or {}),
            "generation_mode": "failed",
            "generation_error": error[:1000],
            "generation_failed_at": now.isoformat(),
        }
        post.updated_at = now
        await self.session.commit()
        self.session.expire_all()

    async def update_post(
        self,
        post_id: UUID,
        *,
        user_id: UUID,
        request: InsightPostUpdateRequest,
    ) -> InsightPostDetail:
        post = await self._get_post_model(post_id, user_id=user_id)
        if not post:
            raise ValueError("인사이트 글을 찾을 수 없습니다")

        if request.title is not None:
            post.title = request.title.strip() or post.title
        if request.subtitle is not None:
            post.subtitle = request.subtitle.strip() or None
        if request.body_md is not None:
            post.body_md = request.body_md
        if request.status is not None:
            post.status = request.status
        if request.metadata is not None:
            post.metadata_ = {**(post.metadata_ or {}), **request.metadata}
        post.updated_at = datetime.now(timezone.utc)

        post_id = post.id
        await self.session.commit()
        self.session.expire_all()
        return await self.get_post(post_id, user_id=user_id)

    async def run_research(
        self,
        post_id: UUID,
        *,
        user_id: UUID,
        request: InsightResearchRequest,
    ) -> InsightPostDetail:
        post = await self._get_post_model(post_id, user_id=user_id)
        if not post:
            raise ValueError("인사이트 글을 찾을 수 없습니다")

        query = (request.query or "").strip()
        if not query:
            research_queries = (post.metadata_ or {}).get("research_queries") or []
            query = research_queries[0] if research_queries else post.title

        try:
            raw_results = await search_web(
                query,
                settings=self.settings,
                limit=min(max(request.max_results * 3, request.max_results), 10),
                categories="general",
                language="ko-KR",
            )
        except WebSearchError as exc:
            raise ValueError(f"조사 검색 실패: {exc}") from exc

        results = _filter_research_results(raw_results, query)[: request.max_results]
        if not results:
            results = raw_results[: request.max_results]

        now = datetime.now(timezone.utc)
        created = []
        existing_urls = {
            _normalize_evidence_url(evidence.url)
            for evidence in post.evidences
            if evidence.source_type == "web" and evidence.url
        }
        for result in results:
            url_key = _normalize_evidence_url(result.get("url"))
            if url_key and url_key in existing_urls:
                continue

            evidence = models.InsightEvidence(
                post_id=post.id,
                source_type="web",
                title=result.get("title") or "검색 결과",
                url=result.get("url"),
                snippet=result.get("snippet"),
                reliability_score=None,
                metadata_={
                    "query": query,
                    "engine": result.get("engine"),
                    "position": result.get("position"),
                },
                created_at=now,
            )
            self.session.add(evidence)
            created.append(evidence)
            if url_key:
                existing_urls.add(url_key)

        if request.append_to_body and created:
            links_md = "\n".join(
                f"- [{e.title}]({e.url})"
                + (f": {e.snippet[:180]}" if e.snippet else "")
                for e in created
                if e.url
            )
            if links_md:
                post.body_md = (
                    post.body_md.rstrip()
                    + "\n\n## 추가 조사 링크\n"
                    + f"검색어: `{query}`\n\n"
                    + links_md
                    + "\n"
                )

        metadata = {**(post.metadata_ or {})}
        metadata["last_research_query"] = query
        post.metadata_ = metadata
        post.updated_at = now

        post_id = post.id
        await self.session.commit()
        self.session.expire_all()
        return await self.get_post(post_id, user_id=user_id)

    async def _get_post_model(
        self, post_id: UUID, *, user_id: UUID
    ) -> models.InsightPost | None:
        stmt = (
            select(models.InsightPost)
            .options(
                selectinload(models.InsightPost.source_file)
                .selectinload(models.File.content)
                .selectinload(models.Content.transcription_result),
                selectinload(models.InsightPost.evidences),
                selectinload(models.InsightPost.annotations),
            )
            .where(
                models.InsightPost.id == post_id,
                models.InsightPost.user_id == user_id,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _generate_post_payload(
        self,
        *,
        source: models.File,
        summary_md: str,
        transcript_text: str,
        request: InsightPostCreateRequest,
    ) -> dict[str, Any]:
        source_title = source.content.title if source.content else None
        title = source_title or source.filename
        target_length = _normalize_target_length(request.target_length)
        transcript_excerpt = transcript_text[:_transcript_char_limit(target_length)]
        summary_excerpt = summary_md[:MAX_SUMMARY_CHARS]

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 영상 전사와 요약을 바탕으로 읽을 만한 한국어 블로그 글을 쓰는 편집자입니다. "
                    "단순 요약이 아니라 핵심 주장, 맥락, 비판적으로 더 볼 지점, 더 탐색할 질문을 재구성하세요. "
                    "없는 사실을 단정하지 말고, 외부 조사가 필요한 부분은 research_queries에 넣으세요."
                    " Write the final article in Korean. Return only valid JSON. "
                    "Write a concise first draft, not a full long-form essay. "
                    "Do not include generic fallback disclaimers, placeholder sections, or ellipses such as '...'."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"[원본 제목]\n{title}\n\n"
                    f"[글 타입]\n{request.post_type}\n\n"
                    f"[톤]\n{request.tone}\n\n"
                    f"[목표 길이]\n{request.target_length}\n\n"
                    f"[기존 요약]\n{summary_excerpt or '(없음)'}\n\n"
                    f"[전사 발췌]\n{transcript_excerpt or '(없음)'}\n\n"
                    "아래 JSON 형식만 출력하세요.\n"
                    "Output constraints: body_md must be under 900 Korean characters, "
                    "use exactly 4 markdown sections, quotes must contain at most 1 item, "
                    "research_queries must contain at most 2 items, and every JSON string must be closed.\n\n"
                    "body_md must use short headings such as '## 핵심 주장'. "
                    "Do not put the full paragraph inside a heading; put paragraphs under headings.\n\n"
                    "```json\n"
                    "{\n"
                    '  "title": "블로그 글 제목",\n'
                    '  "subtitle": "한 줄 부제",\n'
                    '  "body_md": "마크다운 본문. ## 핵심 주장, ## 왜 중요한가, ## 비판적으로 더 볼 지점, ## 더 탐색할 질문만 포함",\n'
                    '  "quotes": [{"text": "영상에서 직접 인용할 만한 120자 이하의 짧은 발언", "timestamp_seconds": 123.0, "speaker": "SPEAKER_00"}],\n'
                    '  "research_queries": ["추가 조사가 필요한 검색어"]\n'
                    "}\n"
                    "```"
                ),
            },
        ]

        try:
            raw = await request_ai_gateway_completion_async(
                settings=self.settings,
                messages=messages,
                model=self.settings.ai_gateway_model_summarize,
                temperature=0.35,
                max_tokens=_output_token_budget(target_length),
                request_timeout_seconds=180,
                max_retry_time=30,
            )
            parsed = _parse_json_response(raw)
            if parsed.get("title") and parsed.get("body_md"):
                body_md = _normalize_body_markdown(str(parsed["body_md"]))
                return {
                    "title": str(parsed["title"]).strip()[:512],
                    "subtitle": str(parsed.get("subtitle") or "").strip()[:1024] or None,
                    "body_md": body_md,
                    "quotes": parsed.get("quotes") or [],
                    "research_queries": parsed.get("research_queries") or [],
                    "generation_mode": "llm",
                    "generation_input": {
                        "summary_chars": len(summary_excerpt),
                        "transcript_chars": len(transcript_excerpt),
                        "output_token_budget": _output_token_budget(target_length),
                    },
                }
            logger.warning("[InsightPost] LLM response missing required fields")
            if not request.allow_fallback:
                raise ValueError("LLM response did not include title/body_md") from None
        except AIGatewayClientError as exc:
            logger.warning("[InsightPost] LLM generation failed: {}", exc)
            if not request.allow_fallback:
                raise ValueError(
                    f"AI Gateway 연결 실패로 동적 글 생성을 완료하지 못했습니다. AI Gateway 설정/상태를 확인하세요. ({exc})"
                ) from exc
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("[InsightPost] LLM response parse failed: {}", exc)
            if not request.allow_fallback:
                raise ValueError(
                    "LLM 응답을 인사이트 글 JSON으로 해석하지 못했습니다."
                ) from exc

        return _fallback_post_payload(
            title=title,
            summary_md=summary_md,
            transcript_text=transcript_text,
            request=request,
        )

    def _to_list_item(self, post: models.InsightPost) -> InsightPostListItem:
        source_title = post.title
        if post.source_file and post.source_file.content:
            source_title = post.source_file.content.title or post.source_file.filename
        elif post.source_file:
            source_title = post.source_file.filename

        return InsightPostListItem(
            id=post.id,
            source_file_id=post.source_file_id,
            source_title=source_title,
            title=post.title,
            subtitle=post.subtitle,
            post_type=post.post_type,
            tone=post.tone,
            status=post.status,
            evidence_count=len(post.evidences or []),
            created_at=post.created_at,
            updated_at=post.updated_at,
        )

    def _to_detail(self, post: models.InsightPost) -> InsightPostDetail:
        item = self._to_list_item(post)
        source = None
        if post.source_file:
            content = post.source_file.content
            transcription = content.transcription_result if content else None
            transcript_text = _extract_transcript_text(
                transcription.transcription if transcription else {}
            )
            source = InsightPostSource(
                id=post.source_file.id,
                title=(content.title if content else None) or post.source_file.filename,
                filename=post.source_file.filename,
                media_url=get_secure_media_url(post.source_file.id),
                source_url=post.source_file.source_url,
                summary_md=content.summary_md if content else None,
                transcript_text=transcript_text,
                duration_seconds=transcription.duration_seconds if transcription else 0.0,
                speakers=transcription.speakers if transcription else [],
            )

        return InsightPostDetail(
            **item.model_dump(),
            body_md=post.body_md,
            metadata=post.metadata_ or {},
            source=source,
            evidences=[
                InsightEvidenceSchema(
                    id=e.id,
                    source_type=e.source_type,
                    title=e.title,
                    url=e.url,
                    snippet=e.snippet,
                    quote_text=e.quote_text,
                    timestamp_seconds=e.timestamp_seconds,
                    reliability_score=e.reliability_score,
                    metadata=e.metadata_ or {},
                    created_at=e.created_at,
                )
                for e in (post.evidences or [])
            ],
            annotations=[
                InsightAnnotationSchema(
                    id=a.id,
                    anchor_text=a.anchor_text,
                    evidence_ids=a.evidence_ids or [],
                    note=a.note,
                    created_at=a.created_at,
                )
                for a in (post.annotations or [])
            ],
        )


def _extract_transcript_text(transcription: dict[str, Any]) -> str:
    text = str(transcription.get("text") or "").strip()
    if text:
        return text

    segments = transcription.get("segments") or []
    lines = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = _coerce_float(segment.get("start"))
        speaker = segment.get("speaker") or ""
        body = str(segment.get("text") or "").strip()
        if not body:
            continue
        prefix = _format_timestamp(start) if start is not None else ""
        if speaker:
            prefix = f"{prefix} {speaker}".strip()
        lines.append(f"{prefix}: {body}" if prefix else body)
    return "\n".join(lines)


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _normalize_body_markdown(body_md: str) -> str:
    text = body_md.strip()
    text = re.sub(r"\s+###\s+", "\n\n### ", text)
    text = re.sub(r"\s+##\s+", "\n\n## ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _repair_heading_only_markdown(text.strip())


def _repair_heading_only_markdown(body_md: str) -> str:
    lines = [line.strip() for line in body_md.splitlines() if line.strip()]
    if len(lines) < 3 or not all(line.startswith("#") for line in lines):
        return body_md

    entries = [re.sub(r"^#{1,3}\s+", "", line).strip() for line in lines]
    if not all(entries):
        return body_md

    sections = [
        ("핵심 주장", entries[0]),
        ("왜 중요한가", entries[1]),
        ("비판적으로 볼 점", entries[2]),
    ]
    if len(entries) > 3:
        question_items = "\n".join(f"- {entry}" for entry in entries[3:6])
        sections.append(("더 탐색할 질문", question_items))

    return "\n\n".join(f"## {heading}\n\n{content}" for heading, content in sections)


def _fallback_post_payload(
    *,
    title: str,
    summary_md: str,
    transcript_text: str,
    request: InsightPostCreateRequest,
) -> dict[str, Any]:
    seed = summary_md.strip() or transcript_text[:2500].strip()
    if not seed:
        seed = "원본 전사와 요약이 충분하지 않아 본문 초안을 만들 수 없습니다."

    body = (
        f"## 이 영상의 핵심 주장\n\n{seed[:1200]}\n\n"
        "## 왜 중요한가\n\n"
        "이 영상은 단순한 정보 전달보다, 주제의 배경과 실제 판단 기준을 다시 살펴볼 계기를 제공합니다. "
        "자동 생성 초안이므로 중요한 사실관계는 추가 조사를 통해 확인해야 합니다.\n\n"
        "## 비판적으로 더 볼 지점\n\n"
        "- 영상 속 주장이 어떤 근거에 기대고 있는지 확인해야 합니다.\n"
        "- 반대 사례나 최신 자료가 있는지 별도 검색이 필요합니다.\n"
        "- 발화자의 관점과 이해관계가 결론에 영향을 주는지 살펴볼 필요가 있습니다.\n\n"
        "## 더 탐색할 질문\n\n"
        "- 이 주장과 충돌하는 연구나 사례는 무엇인가?\n"
        "- 최근 기사나 보고서는 같은 현상을 어떻게 설명하는가?\n"
        "- 이 내용을 내 의사결정이나 학습 주제로 바꾸면 무엇이 남는가?\n"
    )
    return {
        "title": title[:512],
        "subtitle": f"{request.post_type} 관점으로 재구성한 영상 인사이트",
        "body_md": body,
        "quotes": [],
        "research_queries": [title],
        "generation_mode": "fallback",
        "generation_input": {
            "summary_chars": min(len(summary_md), MAX_SUMMARY_CHARS),
            "transcript_chars": min(
                len(transcript_text), _transcript_char_limit(request.target_length)
            ),
            "output_token_budget": _output_token_budget(request.target_length),
        },
    }


def _normalize_target_length(value: str) -> str:
    normalized = (value or "medium").strip().lower()
    if normalized in OUTPUT_TOKENS_BY_TARGET_LENGTH:
        return normalized
    return "medium"


def _transcript_char_limit(target_length: str) -> int:
    return TRANSCRIPT_CHARS_BY_TARGET_LENGTH[_normalize_target_length(target_length)]


def _output_token_budget(target_length: str) -> int:
    return OUTPUT_TOKENS_BY_TARGET_LENGTH[_normalize_target_length(target_length)]


_RESEARCH_STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "the",
    "this",
    "with",
    "what",
    "why",
    "about",
    "based",
    "using",
    "대한",
    "관련",
    "분석",
    "조사",
}


def _filter_research_results(
    results: list[dict[str, Any]], query: str
) -> list[dict[str, Any]]:
    query_tokens = _tokenize_research_text(query)
    if not query_tokens:
        return results

    filtered: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for result in results:
        url = str(result.get("url") or "").split("#", 1)[0].rstrip("/")
        if url and url in seen_urls:
            continue

        title_tokens = _tokenize_research_text(str(result.get("title") or ""))
        text_tokens = _tokenize_research_text(
            " ".join(
                str(result.get(field) or "")
                for field in ("title", "snippet", "url")
            )
        )
        overlap = query_tokens & text_tokens
        title_overlap = query_tokens & title_tokens
        if len(overlap) >= 2 or (title_overlap and len(overlap) >= 1):
            filtered.append(result)
            if url:
                seen_urls.add(url)

    return filtered


def _normalize_evidence_url(value: Any) -> str:
    return str(value or "").split("#", 1)[0].rstrip("/")


def _tokenize_research_text(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9가-힣]{2,}", value.lower())
        if token not in _RESEARCH_STOPWORDS
    }


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"
