from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import get_settings
from .base import Base

settings = get_settings()

# 연결 풀 설정 추가
# - pool_pre_ping: 연결 사용 전 유효성 확인 (끊어진 연결 자동 재연결)
# - pool_recycle: 연결 재활용 시간 (초) - PostgreSQL 기본 timeout보다 짧게
# - pool_size: 기본 연결 수
# - max_overflow: 추가 연결 허용 수
engine = create_async_engine(
    settings.postgres_dsn,
    echo=settings.debug,
    future=True,
    pool_pre_ping=True,
    pool_recycle=300,  # 5분마다 연결 재활용
    pool_size=10,
    max_overflow=20,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# 백그라운드 태스크에서 사용할 세션 팩토리 (FastAPI 의존성 외부에서 사용)
async_session_factory = AsyncSessionLocal


async def get_session() -> AsyncSession:
    """FastAPI 의존성에서 사용할 세션 생성기."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """테스트용 DB 초기화."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


