#!/usr/bin/env python3
"""기존 콘텐츠 데이터 삭제 스크립트."""

import sys
from pathlib import Path

# backend 디렉토리를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
from app.db.session import AsyncSessionLocal
from app.db.models import Content


async def clear_all_content():
    """모든 콘텐츠 데이터 삭제."""
    async with AsyncSessionLocal() as session:
        # 모든 콘텐츠 조회
        from sqlalchemy import select
        stmt = select(Content)
        result = await session.execute(stmt)
        contents = result.scalars().all()
        
        count = len(contents)
        if count == 0:
            print("삭제할 데이터가 없습니다.")
            return
        
        print(f"총 {count}개의 콘텐츠를 삭제합니다...")
        
        # 확인
        response = input("정말로 모든 데이터를 삭제하시겠습니까? (yes/no): ")
        if response.lower() != "yes":
            print("취소되었습니다.")
            return
        
        # 모든 콘텐츠 삭제 (CASCADE로 로그도 함께 삭제됨)
        for content in contents:
            await session.delete(content)
        
        await session.commit()
        print(f"✓ {count}개의 콘텐츠가 삭제되었습니다.")


if __name__ == "__main__":
    asyncio.run(clear_all_content())

