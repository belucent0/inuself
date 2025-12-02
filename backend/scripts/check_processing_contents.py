#!/usr/bin/env python
"""PROCESSING 상태인 콘텐츠 확인 스크립트."""
import asyncio
import sys
import os
from datetime import datetime

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.db.models import ContentStatus
from app.db.session import AsyncSessionLocal
from app.repositories.content_repository import ContentRepository


async def check_processing_contents():
    """PROCESSING 상태인 콘텐츠를 확인합니다."""
    session = AsyncSessionLocal()
    try:
        repo = ContentRepository(session)
        
        # PROCESSING 상태인 콘텐츠 조회
        from sqlalchemy import select
        from app.db.models import Content
        
        stmt = select(Content).where(Content.status == ContentStatus.PROCESSING)
        result = await session.execute(stmt)
        contents = result.scalars().all()
        
        print("=" * 60)
        print(f"PROCESSING 상태인 콘텐츠: {len(contents)}개")
        print("=" * 60)
        
        if contents:
            for content in contents:
                print(f"\nContent ID: {content.id}")
                print(f"  파일명: {content.filename}")
                print(f"  상태: {content.status}")
                print(f"  생성일시: {content.created_at}")
                
                # 로그 확인 (별도 쿼리로)
                from app.db.models import SttLog
                from sqlalchemy import select
                log_stmt = select(SttLog).where(SttLog.content_id == content.id).order_by(SttLog.created_at.desc()).limit(3)
                log_result = await session.execute(log_stmt)
                logs = log_result.scalars().all()
                if logs:
                    print(f"  최근 로그:")
                    for log in logs:
                        print(f"    - {log.created_at}: {log.message}")
        else:
            print("\nPROCESSING 상태인 콘텐츠가 없습니다.")
        
        # QUEUED 상태인 콘텐츠도 확인
        stmt = select(Content).where(Content.status == ContentStatus.QUEUED)
        result = await session.execute(stmt)
        queued_contents = result.scalars().all()
        
        print("\n" + "=" * 60)
        print(f"QUEUED 상태인 콘텐츠: {len(queued_contents)}개")
        print("=" * 60)
        
        if queued_contents:
            for content in queued_contents:
                print(f"\nContent ID: {content.id}")
                print(f"  파일명: {content.filename}")
                print(f"  상태: {content.status}")
                print(f"  생성일시: {content.created_at}")
        else:
            print("\nQUEUED 상태인 콘텐츠가 없습니다.")
        
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(check_processing_contents())

