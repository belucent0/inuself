"""데이터베이스에 SUMMARY_QUEUED enum 값을 직접 추가 (asyncpg 사용)"""
import asyncio
import os
import asyncpg

async def main():
    # .env 파일에서 DATABASE_URL 읽기
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        # 하드코딩된 기본값 (필요시 수정)
        database_url = "postgresql://postgres:postgres@localhost:5432/stt_db"
    
    print(f"\n데이터베이스 연결 중: {database_url.split('@')[1] if '@' in database_url else 'localhost'}")
    
    try:
        # asyncpg로 직접 연결
        conn = await asyncpg.connect(database_url)
        
        print("\n=== SUMMARY_QUEUED 상태를 FileStatus enum에 추가합니다 ===\n")
        
        # PostgreSQL enum에 새 값 추가
        await conn.execute(
            "ALTER TYPE filestatus ADD VALUE IF NOT EXISTS 'SUMMARY_QUEUED'"
        )
        
        print("✓ SUMMARY_QUEUED 상태가 성공적으로 추가되었습니다.\n")
        
        # 확인: 현재 enum 값들 조회
        rows = await conn.fetch("""
            SELECT e.enumlabel 
            FROM pg_type t 
            JOIN pg_enum e ON t.oid = e.enumtypid 
            WHERE t.typname = 'filestatus'
            ORDER BY e.enumsortorder
        """)
        
        print("현재 FileStatus enum 값 목록:")
        for row in rows:
            print(f"  - {row['enumlabel']}")
        print()
        
        await conn.close()
        
    except Exception as e:
        print(f"\n오류 발생: {e}\n")
        raise

if __name__ == "__main__":
    asyncio.run(main())
