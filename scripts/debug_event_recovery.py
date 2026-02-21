import asyncio
import os
import json
import logging
import sys
from datetime import datetime
from redis.asyncio import Redis

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 설정
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RESULT_STREAM = "stream:worker:results"
CONSUMER_GROUP = "backend"

async def simulate_event_loss_and_recovery():
    """이벤트 유실 및 복구 시뮬레이션"""
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    
    # 1. 가짜 완료 이벤트 발행 (ASR & OCR)
    logger.info("Step 1: Publishing fake completed events...")
    
    # ASR 이벤트
    asr_file_id = 999901
    asr_event = {
        "type": "asr",
        "event": "completed",
        "file_id": asr_file_id,
        "result_s3_key": f"results/asr/{asr_file_id}/test.json",
        "duration_seconds": 12.5,
        "num_speakers": 2,
        "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # OCR 이벤트
    ocr_file_id = 999902
    ocr_event = {
        "type": "ocr",
        "event": "completed",
        "file_id": ocr_file_id,
        "result_s3_key": f"results/ocr/{ocr_file_id}/test.json",
        "page_count": 3,
        "text_length": 1500,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Redis Stream에 추가 (JSON 직렬화 주의)
    asr_id = await redis.xadd(RESULT_STREAM, {"data": json.dumps(asr_event)})
    ocr_id = await redis.xadd(RESULT_STREAM, {"data": json.dumps(ocr_event)})
    
    logger.info(f"Published ASR event: {asr_id}")
    logger.info(f"Published OCR event: {ocr_id}")
    
    # 2. 강제로 읽고 ACK 안 함 (Pending 상태 만들기)
    # 실제 환경에서는 Consumer가 읽다가 죽은 상황을 시뮬레이션하기 위해
    # 여기서 XREADGROUP을 호출하되, 우리가 테스트하려는 Consumer Name으로 읽어야 함.
    # 하지만 여기서는 StreamConsumer가 재시작될 때 '자신의' Pending 메시지를 읽으므로,
    # 실제 백엔드 Consumer가 사용하는 이름을 알아내거나, 
    # 혹은 테스트를 위해 임의의 이름을 사용하고 StreamConsumer 로직이 '모든' Consumer의 Pending을 읽는지 확인해야 함.
    # 현재 구현은 `self.consumer_name` (backend-HOSTNAME)만 읽으므로, 
    # 로컬 테스트에서는 정확히 그 이름으로 읽어둬야 함.
    
    hostname = socket.gethostname()
    target_consumer_name = f"backend-{hostname}"
    
    logger.info(f"Step 2: Simulating crash for consumer '{target_consumer_name}'...")
    
    # 그룹 생성 (없으면)
    try:
        await redis.xgroup_create(RESULT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    # 해당 컨슈머 이름으로 메시지 읽기 (ACK 없음)
    # COUNT를 10으로 넉넉히 주어 위에서 발행한 메시지들을 가져오게 함
    # '>' : 아직 아무에게도 전달되지 않은 메시지 읽기
    messages = await redis.xreadgroup(
        groupname=CONSUMER_GROUP,
        consumername=target_consumer_name,
        streams={RESULT_STREAM: ">"},
        count=10
    )
    
    pending_count = 0
    if messages:
        for stream, entries in messages:
            for entry_id, data in entries:
                if entry_id in [asr_id, ocr_id]:
                    logger.info(f"Consumer '{target_consumer_name}' received message {entry_id} but crashed (No ACK).")
                    pending_count += 1
    
    if pending_count == 0:
        logger.warning("Could not read messages as target consumer. Maybe they were already read?")
    
    # 3. Pending 상태 확인
    pending_info = await redis.xpending(RESULT_STREAM, CONSUMER_GROUP)
    logger.info(f"Step 3: Pending messages count: {pending_info['pending']}")
    
    if pending_info['pending'] > 0:
        logger.info("✅ Verified: Messages are pending. Now restart the backend server to test recovery.")
        logger.info("Check backend logs for '[Recovery] Reprocessing pending message'")
    else:
        logger.error("❌ Failed to create pending messages.")

    await redis.close()

if __name__ == "__main__":
    import socket
    asyncio.run(simulate_event_loss_and_recovery())
