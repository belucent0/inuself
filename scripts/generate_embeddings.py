"""
기존 PostgreSQL Content 테이블의 모든 문서에 대해
임베딩을 생성하고 PostgreSQL에 저장

실행: python scripts/generate_embeddings.py
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
project_root = Path(__file__).parent.parent
backend_path = project_root / "backend"
sys.path.insert(0, str(backend_path))

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.db.models import Content
from app.core.config import get_settings
from app.utils.embedding import create_embedding, warmup_embedding_service
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 설정 로드
settings = get_settings()


async def generate_and_store_embeddings():
    """모든 문서의 임베딩 생성 및 저장"""

    # PostgreSQL 연결
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True
    )

    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # 1. 임베딩이 없는 모든 문서 가져오기
        stmt = select(Content).where(Content.embedding == None)
        result = await session.execute(stmt)
        contents = result.scalars().all()

        if not contents:
            logger.info("No contents to process (all have embeddings)")
            return

        logger.info(f"Found {len(contents)} documents without embeddings")

        # 2. FLM 서비스 Warmup (첫 로딩 대기)
        logger.info("Warming up FLM embedding service...")
        warmup_success = await warmup_embedding_service(timeout=30.0)
        if not warmup_success:
            logger.error("Failed to warm up embedding service. Please check ai-gateway / ai-embedding container.")
            return

        # 3. 배치 처리
        success_count = 0
        failed_count = 0

        for idx, content in enumerate(contents, 1):
            try:
                # 제목 + 내용 결합 (임베딩 품질 향상)
                # 최대 2000자까지만 사용 (긴 문서는 요약 사용)
                title = content.title or ""
                summary = content.summary_md or ""
                text = f"{title}\n\n{summary}"[:2000]

                if not text.strip():
                    logger.warning(f"Content {content.id} has no text, skipping")
                    failed_count += 1
                    continue

                # 임베딩 생성 (유틸리티 함수 사용)
                embedding = await create_embedding(text)

                if embedding:
                    # PostgreSQL에 저장
                    stmt = (
                        update(Content)
                        .where(Content.id == content.id)
                        .values(embedding=embedding)
                    )
                    await session.execute(stmt)
                    await session.commit()

                    success_count += 1
                    logger.info(
                        f"[{idx}/{len(contents)}] "
                        f"✅ Generated embedding for: {content.title[:50]}"
                    )
                else:
                    failed_count += 1
                    logger.error(
                        f"[{idx}/{len(contents)}] "
                        f"❌ Failed to generate embedding for: {content.title[:50]}"
                    )

                # API 레이트 리미트 방지
                await asyncio.sleep(0.1)

            except Exception as e:
                failed_count += 1
                logger.error(f"Error processing content {content.id}: {e}")
                continue

            # 진행률 출력 (10개마다)
            if idx % 10 == 0:
                logger.info(
                    f"Progress: {idx}/{len(contents)} "
                    f"(Success: {success_count}, Failed: {failed_count})"
                )

        logger.info("=" * 60)
        logger.info("✅ Embedding generation completed!")
        logger.info(f"Total: {len(contents)}")
        logger.info(f"Success: {success_count}")
        logger.info(f"Failed: {failed_count}")
        logger.info("=" * 60)


async def create_hnsw_index():
    """HNSW 인덱스 생성 (임베딩 생성 후 실행)"""

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True
    )

    async with engine.begin() as conn:
        # 인덱스 존재 확인
        result = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'content' AND indexname = 'content_embedding_hnsw_idx';")
        )
        existing_index = result.fetchone()

        if existing_index:
            logger.info("HNSW index already exists, skipping creation")
            return

        logger.info("Creating HNSW index (this may take a few minutes)...")

        # HNSW 인덱스 생성
        # m=16: HNSW 그래프의 링크 수 (기본값 16, 높을수록 정확하지만 메모리 사용 증가)
        # ef_construction=64: 인덱스 빌드 시 탐색 깊이 (기본값 64)
        await conn.execute(
            text("""
            CREATE INDEX content_embedding_hnsw_idx
            ON content
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """)
        )

        logger.info("✅ HNSW index created successfully!")
        logger.info("Index: content_embedding_hnsw_idx")
        logger.info("Algorithm: HNSW (m=16, ef_construction=64)")
        logger.info("Distance: Cosine similarity")


async def verify_setup():
    """설정 검증"""

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True
    )

    async with engine.begin() as conn:
        # 1. 임베딩 개수 확인
        result = await conn.execute(
            text("SELECT COUNT(*) FROM content WHERE embedding IS NOT NULL;")
        )
        embedding_count = result.fetchone()[0]

        # 2. 전체 문서 수 확인
        result = await conn.execute(text("SELECT COUNT(*) FROM content;"))
        total_count = result.fetchone()[0]

        # 3. 인덱스 확인
        result = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'content' AND indexname LIKE '%embedding%';")
        )
        indexes = [row[0] for row in result.fetchall()]

        logger.info("=" * 60)
        logger.info("📊 Setup Verification")
        logger.info("=" * 60)
        logger.info(f"Total documents: {total_count}")
        logger.info(f"Documents with embeddings: {embedding_count}")
        logger.info(f"Coverage: {embedding_count/total_count*100:.1f}%")
        logger.info(f"Indexes: {indexes or 'None'}")
        logger.info("=" * 60)

        if embedding_count == 0:
            logger.warning("⚠️ No embeddings found. Run with --generate first.")
        elif embedding_count < total_count:
            logger.warning(f"⚠️ {total_count - embedding_count} documents missing embeddings")

        if not indexes:
            logger.warning("⚠️ No indexes found. Run with --create-index")


async def main():
    """메인 함수"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate embeddings for Content table")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate embeddings for documents without embeddings"
    )
    parser.add_argument(
        "--create-index",
        action="store_true",
        help="Create HNSW index on embedding column"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify setup (check embeddings and indexes)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all steps: generate → create-index → verify"
    )

    args = parser.parse_args()

    if args.all:
        logger.info("Running all steps...")
        await generate_and_store_embeddings()
        await create_hnsw_index()
        await verify_setup()
    elif args.generate:
        await generate_and_store_embeddings()
    elif args.create_index:
        await create_hnsw_index()
    elif args.verify:
        await verify_setup()
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
