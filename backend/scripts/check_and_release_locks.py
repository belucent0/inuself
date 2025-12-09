#!/usr/bin/env python
"""Redis 락 상태 확인 및 해제 스크립트."""
import sys
import os

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.core.config import get_settings
from app.core.redis import get_redis_connection

settings = get_settings()

def format_ttl(ttl_seconds: int) -> str:
    """TTL을 읽기 쉬운 형식으로 변환."""
    if ttl_seconds == -1:
        return "TTL 없음 (영구)"
    elif ttl_seconds == -2:
        return "키 없음"
    elif ttl_seconds < 0:
        return f"알 수 없음 ({ttl_seconds})"
    
    hours = ttl_seconds // 3600
    minutes = (ttl_seconds % 3600) // 60
    seconds = ttl_seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}초")
    
    return " ".join(parts)

def check_locks():
    """모든 락 상태 확인."""
    try:
        redis_client = get_redis_connection()
        if not redis_client:
            print("Redis 연결 실패")
            return
        
        print("=" * 60)
        print("Redis 락 상태 확인")
        print("=" * 60)
        
        # 확인할 락 패턴들
        lock_patterns = [
            "lock:asr:global",
            "lock:llm:global",
            "lock:ocr:global",
            "lock:llm_server:global",  # LLM API 서버 실행 락
        ]
        
        # 개별 락도 확인 (최대 20개)
        individual_patterns = [
            "lock:asr:*",
            "lock:llm:*",
            "lock:ocr:*",
        ]
        
        print("\n[전역 락 상태]")
        for lock_key in lock_patterns:
            exists = redis_client.exists(lock_key)
            if exists:
                ttl = redis_client.ttl(lock_key)
                ttl_str = format_ttl(ttl)
                print(f"  {lock_key}:")
                print(f"    상태: 잠금됨")
                print(f"    TTL: {ttl_str} ({ttl}초)")
            else:
                print(f"  {lock_key}: 해제됨")
        
        print("\n[개별 락 상태 (최대 20개)]")
        for pattern in individual_patterns:
            # Redis에서 패턴으로 키 검색
            keys = []
            cursor = 0
            while True:
                cursor, partial_keys = redis_client.scan(cursor, match=pattern, count=100)
                keys.extend(partial_keys)
                if cursor == 0:
                    break
                if len(keys) >= 20:  # 최대 20개만 표시
                    break
            
            if keys:
                print(f"\n  패턴: {pattern}")
                for key in keys[:20]:  # 최대 20개만 표시
                    ttl = redis_client.ttl(key)
                    ttl_str = format_ttl(ttl)
                    print(f"    {key}:")
                    print(f"      TTL: {ttl_str} ({ttl}초)")
            else:
                print(f"\n  패턴: {pattern} - 락 없음")
        
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"오류 발생: {e}")
        import traceback
        traceback.print_exc()

def release_lock(lock_key: str):
    """특정 락을 강제로 해제."""
    try:
        redis_client = get_redis_connection()
        if not redis_client:
            print("Redis 연결 실패")
            return False
        
        exists = redis_client.exists(lock_key)
        if not exists:
            print(f"락 '{lock_key}'는 이미 해제되어 있습니다.")
            return True
        
        # Redis Lock 객체를 생성하여 해제 시도
        from redis.lock import Lock
        lock = Lock(redis_client, lock_key)
        
        try:
            lock.release()
            print(f"락 '{lock_key}'를 성공적으로 해제했습니다.")
            return True
        except Exception as e:
            # Lock 객체로 해제 실패 시 직접 키 삭제 시도
            print(f"Lock 객체로 해제 실패, 키를 직접 삭제 시도: {e}")
            deleted = redis_client.delete(lock_key)
            if deleted:
                print(f"락 '{lock_key}'를 강제로 삭제했습니다.")
                return True
            else:
                print(f"락 '{lock_key}' 삭제 실패")
                return False
                
    except Exception as e:
        print(f"락 해제 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return False

def release_all_global_locks():
    """모든 전역 락을 해제."""
    global_locks = [
        "lock:asr:global",
        "lock:llm:global",
        "lock:ocr:global",
        "lock:llm_server:global",
    ]
    
    print("=" * 60)
    print("모든 전역 락 해제")
    print("=" * 60)
    
    for lock_key in global_locks:
        print(f"\n[{lock_key}]")
        release_lock(lock_key)
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "check":
            check_locks()
        elif command == "release":
            if len(sys.argv) > 2:
                lock_key = sys.argv[2]
                release_lock(lock_key)
            else:
                print("사용법: python check_and_release_locks.py release <lock_key>")
                print("예: python check_and_release_locks.py release lock:ocr:global")
        elif command == "release-all":
            release_all_global_locks()
        else:
            print("알 수 없는 명령어:", command)
            print("\n사용법:")
            print("  python check_and_release_locks.py check              # 락 상태 확인")
            print("  python check_and_release_locks.py release <lock_key> # 특정 락 해제")
            print("  python check_and_release_locks.py release-all         # 모든 전역 락 해제")
    else:
        check_locks()
        print("\n사용법:")
        print("  python check_and_release_locks.py check              # 락 상태 확인")
        print("  python check_and_release_locks.py release <lock_key> # 특정 락 해제")
        print("  python check_and_release_locks.py release-all         # 모든 전역 락 해제")

