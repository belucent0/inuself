import asyncio
import sys
sys.path.insert(0, 'backend')

from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import File, FileStatus
from sqlalchemy import select, func

async def main():
    session = AsyncSessionLocal()
    try:
        print("\n=== 파일 상태 요약 ===")
        for status in FileStatus:
            stmt = select(func.count()).select_from(File).where(File.status == status)
            result = await session.execute(stmt)
            count = result.scalar()
            if count > 0:
                print(f"{status.value}: {count}개")
        
        # SUMMARIZING 상태 파일 상세 확인
        stmt = select(File).where(File.status == FileStatus.SUMMARIZING)
        result = await session.execute(stmt)
        files = result.scalars().all()
        
        if files:
            print(f"\n=== SUMMARIZING 파일 상세 ({len(files)}개) ===")
            for f in files:
                print(f"\nID: {f.id}")
                print(f"  파일: {f.filename}")
                print(f"  요약 존재: {'예' if f.summary_md else '아니오'}")
                print(f"  요약 길이: {len(f.summary_md or '')} chars")
                print(f"  제목: {f.title or '없음'}")
        else:
            print("\n✓ SUMMARIZING 상태 파일 없음 (정상)")
            
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
