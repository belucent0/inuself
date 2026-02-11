from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import get_settings
from .base import Base

settings = get_settings()

engine = create_async_engine(settings.postgres_dsn, echo=settings.debug, future=True)
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


