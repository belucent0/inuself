#!/usr/bin/env python
"""LM Studio 상태 확인 스크립트."""
import sys
import httpx
import time

LMSTUDIO_URL = "http://localhost:1234/v1/models"
MAX_RETRIES = 3
RETRY_DELAY = 2

def check_lmstudio():
    """LM Studio API가 응답하는지 확인."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(LMSTUDIO_URL)
                if response.status_code == 200:
                    print(f"✓ LM Studio is running (attempt {attempt}/{MAX_RETRIES})")
                    return True
        except Exception as e:
            print(f"✗ LM Studio not responding (attempt {attempt}/{MAX_RETRIES}): {e}")
        
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    
    return False

if __name__ == "__main__":
    if check_lmstudio():
        sys.exit(0)
    else:
        print("\n⚠ LM Studio가 실행되지 않았습니다!")
        print("다음을 확인하세요:")
        print("  1. LM Studio 애플리케이션이 실행 중인지")
        print("  2. 모델이 로드되어 있는지")
        print("  3. Local Server가 시작되었는지 (포트 1234)")
        sys.exit(1)






