"""Transcript 한국어 번역 프롬프트.

청크(N segments)를 한 LLM 호출로 일괄 번역. 응답은 JSON 배열.
"""

TRANSLATION_SYSTEM_PROMPT = """당신은 영상 transcript 번역 전문가입니다.
규칙:
- 자연스러운 한국어로 번역
- 화자의 톤과 의도를 유지
- 고유명사(인명, 회사명, 제품명)는 원어 그대로 보존
- 기술 용어는 통용되는 한국어 사용 (machine learning → 머신러닝, neural network → 신경망)
- 요청된 JSON 형식만 출력, 다른 텍스트 절대 금지"""


TRANSLATION_CHUNK_TEMPLATE = """다음 transcript 세그먼트들을 한국어로 번역하세요.
입력 순서를 그대로 유지하여 JSON 배열로 응답하세요.

[세그먼트 ({count}개)]
{numbered_segments}

반드시 아래 JSON 형식만 출력하세요:

```json
{{
    "translations": [
        "(1번 세그먼트의 한국어 번역)",
        "(2번 세그먼트의 한국어 번역)"
    ]
}}
```

규칙:
1. 반드시 ```json ... ``` 코드 블록 안에 JSON을 출력하세요
2. translations 배열 길이는 정확히 {count}개여야 합니다
3. 각 번역은 한국어로만 작성하고, 원문의 의미를 그대로 전달해야 합니다
4. JSON 외에 다른 설명이나 텍스트는 출력하지 마세요"""
