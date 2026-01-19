"""Control API Client - Provider Manager Control API 클라이언트.

Redis Stream을 통해 Provider Manager와 통신하는 클라이언트.
Docker 컨테이너나 다른 서비스에서 사용할 수 있습니다.

Usage:
    from clients.control_client import ControlClient

    async with ControlClient() as client:
        # 프로바이더 목록 조회
        providers = await client.list_providers()

        # 상태 조회
        status = await client.get_status()

        # 작업 조회
        jobs = await client.get_jobs()

        # 프로바이더 관리
        await client.load_provider("llama-server")
        await client.unload_provider("llama-server")
        await client.reload_provider("llama-server")
"""

import json
import time
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any

import redis.asyncio as redis_async

logger = logging.getLogger("ControlClient")

# 기본 설정
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_REQUEST_STREAM = "stream:provider:requests"
DEFAULT_RESPONSE_STREAM = "stream:provider:responses"
DEFAULT_TIMEOUT = 30.0


class ControlClient:
    """Provider Manager Control API 클라이언트.

    Redis Stream을 통해 Provider Manager와 통신합니다.
    """

    def __init__(
        self,
        redis_url: str = DEFAULT_REDIS_URL,
        request_stream: str = DEFAULT_REQUEST_STREAM,
        response_stream: str = DEFAULT_RESPONSE_STREAM,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.redis_url = redis_url
        self.request_stream = request_stream
        self.response_stream = response_stream
        self.timeout = timeout
        self.redis: Optional[redis_async.Redis] = None

    async def connect(self):
        """Redis 연결."""
        if not self.redis:
            self.redis = redis_async.from_url(self.redis_url, decode_responses=True)
        logger.debug("ControlClient connected to Redis")

    async def close(self):
        """연결 종료."""
        if self.redis:
            await self.redis.aclose()
            self.redis = None
        logger.debug("ControlClient disconnected")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _send_request(self, action: str, **kwargs) -> Dict[str, Any]:
        """Control API 요청 전송 및 응답 대기.

        Args:
            action: 실행할 액션
            **kwargs: 추가 매개변수

        Returns:
            응답 딕셔너리

        Raises:
            TimeoutError: 응답 대기 시간 초과
            RuntimeError: 요청 처리 실패
        """
        if not self.redis:
            await self.connect()

        request_id = str(uuid.uuid4())
        request_data = {
            "request_id": request_id,
            "action": action,
            "timestamp": str(time.time()),
            **{k: str(v) if not isinstance(v, str) else v for k, v in kwargs.items()},
        }

        # 요청 전송
        await self.redis.xadd(self.request_stream, request_data)
        logger.debug(f"Sent request: action={action}, request_id={request_id}")

        # 응답 대기
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            # Response Stream에서 응답 읽기
            messages = await self.redis.xread(
                {self.response_stream: "0"},
                count=100,
                block=1000,
            )

            if messages:
                for stream_name, stream_messages in messages:
                    for message_id, message_data in stream_messages:
                        if message_data.get("request_id") == request_id:
                            # 메시지 삭제 (처리 완료)
                            await self.redis.xdel(self.response_stream, message_id)

                            if "error" in message_data:
                                raise RuntimeError(message_data["error"])

                            result = message_data.get("result", "{}")
                            return json.loads(result)

        raise TimeoutError(f"Timeout waiting for response (action={action})")

    # ==========================================
    # Provider Management
    # ==========================================

    async def list_providers(self) -> Dict[str, Any]:
        """프로바이더 목록 조회.

        Returns:
            {
                "action": "list_providers",
                "providers": [...],
                "running_count": int
            }
        """
        return await self._send_request("list_providers")

    async def get_status(self) -> Dict[str, Any]:
        """전체 상태 조회.

        Returns:
            {
                "action": "get_status",
                "groups": [...],
                "running_providers": [...],
                "jobs": {...}
            }
        """
        return await self._send_request("get_status")

    async def load_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 로드.

        Args:
            name: 프로바이더 이름

        Returns:
            {"action": "load", "provider": name, "status": "loaded"}
        """
        return await self._send_request("load", name=name)

    async def unload_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 언로드.

        Args:
            name: 프로바이더 이름

        Returns:
            {"action": "unload", "provider": name, "status": "unloaded"}
        """
        return await self._send_request("unload", name=name)

    async def reload_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 재로드.

        Args:
            name: 프로바이더 이름

        Returns:
            {"action": "reload", "provider": name, "status": "reloaded"}
        """
        return await self._send_request("reload", name=name)

    async def start_all(self) -> Dict[str, Any]:
        """모든 프로바이더 시작.

        Returns:
            {"action": "start_all", "status": "started", "running_providers": [...]}
        """
        return await self._send_request("start_all")

    async def stop_all(self) -> Dict[str, Any]:
        """모든 프로바이더 중지.

        Returns:
            {"action": "stop_all", "status": "stopped"}
        """
        return await self._send_request("stop_all")

    # ==========================================
    # Job Management
    # ==========================================

    async def get_jobs(self, provider: str = None, trace_id: str = None) -> Dict[str, Any]:
        """작업 목록 조회.

        Args:
            provider: 프로바이더 이름 (None이면 전체)
            trace_id: 클라이언트 TraceId (분산 추적용)

        Returns:
            {
                "action": "get_jobs",
                "total_active": int,
                "by_provider": {...},
                "by_type": {...},
                "jobs": [...]
            }
        """
        kwargs = {}
        if provider:
            kwargs["provider"] = provider
        if trace_id:
            kwargs["trace_id"] = trace_id
        return await self._send_request("get_jobs", **kwargs)

    async def get_jobs_by_trace(self, trace_id: str) -> Dict[str, Any]:
        """TraceId로 작업 목록 조회.

        분산 추적 시 특정 요청 체인의 모든 작업을 조회합니다.

        Args:
            trace_id: 클라이언트 TraceId

        Returns:
            {
                "action": "get_jobs",
                "trace_id": str,
                "total": int,
                "jobs": [...]
            }
        """
        return await self._send_request("get_jobs", trace_id=trace_id)


# ==========================================
# CLI Interface
# ==========================================

async def main():
    """CLI 테스트."""
    import argparse

    parser = argparse.ArgumentParser(description="Control Client CLI")
    parser.add_argument("action", choices=[
        "list", "status", "jobs", "load", "unload", "reload", "start-all", "stop-all"
    ])
    parser.add_argument("--name", help="Provider name")
    parser.add_argument("--trace-id", help="TraceId for job filtering")
    parser.add_argument("--redis", default=DEFAULT_REDIS_URL, help="Redis URL")

    args = parser.parse_args()

    async with ControlClient(redis_url=args.redis) as client:
        if args.action == "list":
            result = await client.list_providers()
        elif args.action == "status":
            result = await client.get_status()
        elif args.action == "jobs":
            if args.trace_id:
                result = await client.get_jobs_by_trace(args.trace_id)
            else:
                result = await client.get_jobs(provider=args.name)
        elif args.action == "load":
            if not args.name:
                print("Error: --name required")
                return
            result = await client.load_provider(args.name)
        elif args.action == "unload":
            if not args.name:
                print("Error: --name required")
                return
            result = await client.unload_provider(args.name)
        elif args.action == "reload":
            if not args.name:
                print("Error: --name required")
                return
            result = await client.reload_provider(args.name)
        elif args.action == "start-all":
            result = await client.start_all()
        elif args.action == "stop-all":
            result = await client.stop_all()

        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
