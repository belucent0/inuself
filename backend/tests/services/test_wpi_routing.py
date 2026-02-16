"""WPI routing and pair-gap behavior tests."""

import json
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.schemas.wpi import I_TEST_TYPES, ME_TEST_TYPES
from app.services.wpi_service import WpiService


def _service() -> WpiService:
    return WpiService(session=MagicMock())


def _fake_auto_report(i_type: str, me_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        i_type_kr=i_type,
        me_type_kr=me_type,
        basic_need="need",
        strengths="strength",
        weaknesses="weakness",
        personality_description="personality",
        me_specific_analysis="specific",
        me_common_analysis="common",
        me_context_analysis="context",
        default_block_id=f"base:{i_type.lower()}:default",
        specific_block_id=f"pair:{i_type.lower()}:{me_type.lower()}:specific",
        common_block_id=f"common:{me_type.lower()}:base",
        block_ids=(
            f"base:{i_type.lower()}:default",
            f"pair:{i_type.lower()}:{me_type.lower()}:specific",
            f"common:{me_type.lower()}:base",
        ),
    )


def _extract_context_from_user_prompt(user_content: str) -> dict[str, Any]:
    start_marker = "## 입력 데이터"
    end_marker = "## 해석 제약"
    start = user_content.index(start_marker) + len(start_marker)
    end = user_content.index(end_marker)
    raw_json = user_content[start:end].strip()
    return json.loads(raw_json)


def _sample_enriched_data() -> dict[str, Any]:
    return {
        "i_test": {
            "dominant_type": "Realist",
            "scores": {
                "Realist": 10.0,
                "Romanticist": 3.0,
            },
        },
        "me_test": {
            "dominant_type": "Relation",
            "scores": {
                "Relation": 5.0,
                "Trust": 2.0,
            },
        },
        "gap_analysis": {
            "axis_gaps": {
                "relation_recognition": {
                    "i_type": "Realist",
                    "me_type": "Relation",
                    "i_score": 10.0,
                    "me_score": 5.0,
                    "gap": 5.0,
                }
            }
        },
    }


def test_bucketize_pair_gap_ratio_boundaries() -> None:
    service = _service()

    assert service._bucketize_pair_gap_ratio(-0.50) == "very_me_dominant"
    assert service._bucketize_pair_gap_ratio(-0.35) == "very_me_dominant"
    assert service._bucketize_pair_gap_ratio(-0.34) == "me_dominant"
    assert service._bucketize_pair_gap_ratio(-0.15) == "me_dominant"
    assert service._bucketize_pair_gap_ratio(-0.14) == "balanced"
    assert service._bucketize_pair_gap_ratio(0.14) == "balanced"
    assert service._bucketize_pair_gap_ratio(0.15) == "i_dominant"
    assert service._bucketize_pair_gap_ratio(0.34) == "i_dominant"
    assert service._bucketize_pair_gap_ratio(0.35) == "very_i_dominant"


def test_pair_gap_profile_uses_mapped_axis_and_bucket() -> None:
    service = _service()

    profile = service._build_pair_gap_profile(
        i_dom="Realist",
        i_scores={"Realist": 10.0},
        me_scores={"Relation": 5.0},
    )

    assert profile is not None
    assert profile["axis"] == "relation_recognition"
    assert profile["i_type"] == "Realist"
    assert profile["me_type"] == "Relation"
    assert profile["gap"] == 5.0
    assert profile["pair_total"] == 15.0
    assert profile["bucket"] == "i_dominant"
    assert profile["direction"] == "i_test_dominant"
    assert profile["confidence"] == "normal"


def test_build_routing_key_produces_125_unique_keys() -> None:
    service = _service()
    buckets = [
        "very_me_dominant",
        "me_dominant",
        "balanced",
        "i_dominant",
        "very_i_dominant",
    ]

    keys = {
        service._build_routing_key(i_type, me_type, bucket)
        for i_type in I_TEST_TYPES
        for me_type in ME_TEST_TYPES
        for bucket in buckets
    }

    assert len(keys) == 125


def test_collect_selected_block_ids_dedupes_and_appends_gap_block() -> None:
    service = _service()

    selected = service._collect_selected_block_ids(
        primary_profile={
            "block_ids": ["base:realist:default", "pair:realist:relation:specific"]
        },
        secondary_profiles=[
            {
                "profile": {
                    "block_ids": [
                        "pair:realist:relation:specific",
                        "common:relation:base",
                    ]
                }
            }
        ],
        pair_gap_bucket="balanced",
    )

    assert selected == [
        "base:realist:default",
        "pair:realist:relation:specific",
        "common:relation:base",
        "gap:balanced",
    ]


def test_build_ai_report_messages_includes_routing_context(monkeypatch) -> None:
    service = _service()

    monkeypatch.setattr(
        "app.services.wpi_service.get_auto_report",
        lambda i_type, me_type: _fake_auto_report(str(i_type), str(me_type)),
    )

    enriched_data = _sample_enriched_data()

    messages = service._build_ai_report_messages(enriched_data)
    assert len(messages) == 2
    assert messages[1]["role"] == "user"

    context = _extract_context_from_user_prompt(str(messages[1]["content"]))
    routing = context["routing"]
    assert routing["version"] == "v1"
    assert routing["key"] == "rk_v1:Realist:Relation:i_dominant"
    assert routing["rule_id"] == "R1-rea-rel-i_dominant"
    assert routing["pair_gap_bucket"] == "i_dominant"

    assert context["pair_gap_profile"]["bucket"] == "i_dominant"
    assert context["pair_gap_profile"]["gap"] == 5.0

    selected_block_ids = context["selected_block_ids"]
    assert "gap:i_dominant" in selected_block_ids
    assert "base:realist:default" in selected_block_ids


def test_build_routing_rule_id_format() -> None:
    service = _service()
    rule_id = service._build_routing_rule_id("Realist", "Relation", "balanced")
    assert rule_id == "R1-rea-rel-balanced"


def test_build_routing_rule_id_produces_125_unique_values() -> None:
    service = _service()
    buckets = [
        "very_me_dominant",
        "me_dominant",
        "balanced",
        "i_dominant",
        "very_i_dominant",
    ]

    rule_ids = {
        service._build_routing_rule_id(i_type, me_type, bucket)
        for i_type in I_TEST_TYPES
        for me_type in ME_TEST_TYPES
        for bucket in buckets
    }

    assert len(rule_ids) == 125
    assert all(rule_id.startswith("R1-") for rule_id in rule_ids)


def test_worker_context_extractor_parses_backend_messages(monkeypatch) -> None:
    root_dir = Path(__file__).resolve().parents[3]
    root_dir_str = str(root_dir)
    if root_dir_str not in sys.path:
        sys.path.append(root_dir_str)

    processor_module = importlib.import_module("worker.processors.wpi_report_processor")
    extract_context = getattr(processor_module, "_extract_context_from_messages")

    service = _service()
    monkeypatch.setattr(
        "app.services.wpi_service.get_auto_report",
        lambda i_type, me_type: _fake_auto_report(str(i_type), str(me_type)),
    )

    messages = service._build_ai_report_messages(_sample_enriched_data())
    context = extract_context(messages)

    assert context is not None
    assert context["routing"]["key"] == "rk_v1:Realist:Relation:i_dominant"
    assert context["routing"]["rule_id"] == "R1-rea-rel-i_dominant"
    assert context["pair_gap_profile"]["bucket"] == "i_dominant"


def test_rule_based_fallback_report_uses_routing_context(monkeypatch) -> None:
    root_dir = Path(__file__).resolve().parents[3]
    root_dir_str = str(root_dir)
    if root_dir_str not in sys.path:
        sys.path.append(root_dir_str)

    processor_module = importlib.import_module("worker.processors.wpi_report_processor")
    build_fallback_report = getattr(processor_module, "_build_rule_based_wpi_report")

    service = _service()
    monkeypatch.setattr(
        "app.services.wpi_service.get_auto_report",
        lambda i_type, me_type: _fake_auto_report(str(i_type), str(me_type)),
    )

    messages = service._build_ai_report_messages(_sample_enriched_data())
    context = _extract_context_from_user_prompt(str(messages[1]["content"]))
    report_md = build_fallback_report(context, llm_error="No connected db")

    assert "## 1. 종합 해석" in report_md
    assert "## 4. 상황별 제안" in report_md
    assert "### 개인 실행" in report_md
    assert "### 협업/소통" in report_md
    assert "rk_v1:Realist:Relation:i_dominant" in report_md
    assert "R1-rea-rel-i_dominant" in report_md
    assert "gap은 **5.0**" in report_md
