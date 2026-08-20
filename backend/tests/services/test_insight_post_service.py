from app.schemas.insight import InsightPostCreateRequest
from app.services.insight_post_service import (
    _extract_transcript_text,
    _fallback_post_payload,
    _filter_research_results,
    _parse_json_response,
)


def test_extract_transcript_text_prefers_plain_text():
    result = _extract_transcript_text({"text": " 이미 정리된 전사 "})

    assert result == "이미 정리된 전사"


def test_extract_transcript_text_formats_segments():
    result = _extract_transcript_text(
        {
            "segments": [
                {"start": 1.2, "speaker": "SPEAKER_00", "text": "첫 문장"},
                {"start": 65.0, "speaker": "SPEAKER_01", "text": "두 번째 문장"},
            ]
        }
    )

    assert "00:00:01 SPEAKER_00: 첫 문장" in result
    assert "00:01:05 SPEAKER_01: 두 번째 문장" in result


def test_parse_json_response_accepts_fenced_json():
    result = _parse_json_response(
        '```json\n{"title": "제목", "body_md": "본문"}\n```'
    )

    assert result == {"title": "제목", "body_md": "본문"}


def test_fallback_post_payload_contains_critical_sections():
    payload = _fallback_post_payload(
        title="영상 제목",
        summary_md="핵심 요약",
        transcript_text="전사",
        request=InsightPostCreateRequest(),
    )

    assert payload["title"] == "영상 제목"
    assert "## 이 영상의 핵심 주장" in payload["body_md"]
    assert "## 비판적으로 더 볼 지점" in payload["body_md"]
    assert payload["research_queries"] == ["영상 제목"]


def test_filter_research_results_excludes_generic_product_pages():
    results = [
        {
            "title": "Google Gemini",
            "url": "https://gemini.google.com/",
            "snippet": "Google AI assistant for writing and brainstorming.",
        },
        {
            "title": "AI-Generated Code Quality and the Challenges we all face",
            "url": "https://example.com/ai-generated-code-quality",
            "snippet": "Research shows AI-generated code can increase quality issues.",
        },
    ]

    filtered = _filter_research_results(
        results,
        "AI code quality software engineering limitations",
    )

    assert [result["title"] for result in filtered] == [
        "AI-Generated Code Quality and the Challenges we all face"
    ]


def test_filter_research_results_deduplicates_urls():
    results = [
        {
            "title": "AI Code Quality Study",
            "url": "https://example.com/report#abstract",
            "snippet": "AI code quality software engineering limitations.",
        },
        {
            "title": "AI Code Quality Study",
            "url": "https://example.com/report#references",
            "snippet": "AI code quality software engineering limitations.",
        },
    ]

    filtered = _filter_research_results(
        results,
        "AI code quality software engineering limitations",
    )

    assert len(filtered) == 1
