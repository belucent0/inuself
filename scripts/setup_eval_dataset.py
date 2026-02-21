#!/usr/bin/env python3
"""Langfuse 평가 데이터셋 초기 설정 스크립트.

골든 테스트 케이스를 Langfuse Dataset으로 등록합니다.
최초 1회 또는 케이스를 추가/수정할 때 실행합니다.

사용법:
    LANGFUSE_HOST=https://... \\
    LANGFUSE_PUBLIC_KEY=pk-... \\
    LANGFUSE_SECRET_KEY=sk-... \\
        python scripts/setup_eval_dataset.py

    # fixture와 Langfuse 데이터셋을 완전히 동기화 (삭제/추가/수정 모두 반영)
    python scripts/setup_eval_dataset.py --reset

환경변수가 이미 .env에 있다면:
    python scripts/setup_eval_dataset.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from langfuse import Langfuse

DATASET_NAME = "chat-quality-golden-set"
FIXTURES_PATH = Path(__file__).parent.parent / "tests" / "e2e" / "fixtures" / "chat_multiturn_cases.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="기존 항목을 모두 삭제하고 fixture 기준으로 재등록 (삭제/수정 반영 시 사용)",
    )
    args = parser.parse_args()

    langfuse = Langfuse(
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
        host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )

    # 데이터셋 생성 (이미 존재하면 그대로 사용)
    try:
        dataset = langfuse.get_dataset(DATASET_NAME)
        print(f"[OK] 기존 데이터셋 사용: '{DATASET_NAME}' ({len(dataset.items)}개 항목)")
    except Exception:
        langfuse.create_dataset(
            name=DATASET_NAME,
            description="멀티턴 채팅 품질 검증 골든 데이터셋. AI 에이전트 라우팅 및 맥락 처리 회귀 방지.",
        )
        print(f"[OK] 데이터셋 생성: '{DATASET_NAME}'")

    dataset = langfuse.get_dataset(DATASET_NAME)

    # --reset: fixture에 없는 항목 삭제 + 기존 항목 갱신
    if args.reset:
        fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
        valid_ids = {
            f"{suite['id']}__turn{turn_def['turn']}"
            for suite in fixtures["test_suites"]
            for turn_def in suite["turns"]
        }

        removed = 0
        for item in dataset.items:
            case_id = item.metadata.get("case_id") if item.metadata else None
            if case_id not in valid_ids:
                item.delete()
                print(f"  [DEL] {case_id}")
                removed += 1

        print(f"[reset] {removed}개 항목 삭제됨")
        # 삭제 후 데이터셋 재조회
        dataset = langfuse.get_dataset(DATASET_NAME)

    # 기존 항목 ID 수집 (중복 방지)
    existing_ids = {item.metadata.get("case_id") for item in dataset.items if item.metadata}

    # fixtures 파일에서 케이스 로드
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    added = 0

    for suite in fixtures["test_suites"]:
        for turn_def in suite["turns"]:
            case_id = f"{suite['id']}__turn{turn_def['turn']}"

            if case_id in existing_ids:
                print(f"  [SKIP] 이미 존재: {case_id}")
                continue

            assertions = turn_def["assertions"]

            langfuse.create_dataset_item(
                dataset_name=DATASET_NAME,
                input={
                    "suite_id": suite["id"],
                    "turn": turn_def["turn"],
                    "query": turn_def["query"],
                },
                expected_output={
                    "mode": assertions.get("mode", {}).get("expected", []),
                    "content_contains_any": assertions.get("content_contains_any", []),
                    "content_not_contains": assertions.get("content_not_contains", []),
                    "context_check": assertions.get("context_check"),
                    "max_response_time_seconds": assertions.get("max_response_time_seconds", 60),
                },
                metadata={
                    "case_id": case_id,
                    "suite_name": suite["name"],
                    "description": f"Turn {turn_def['turn']}: {turn_def['query'][:40]}",
                },
            )
            print(f"  [ADD] {case_id}: '{turn_def['query']}'")
            added += 1

    langfuse.flush()
    print(f"\n완료: {added}개 항목 추가됨")
    print(f"Langfuse UI에서 확인: {os.environ.get('LANGFUSE_HOST', 'https://cloud.langfuse.com')}")


if __name__ == "__main__":
    main()
