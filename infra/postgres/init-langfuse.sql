-- Langfuse 데이터베이스 초기화
-- 이 스크립트는 PostgreSQL 컨테이너 시작 시 자동 실행됩니다.

-- langfuse 데이터베이스가 없으면 생성
SELECT 'CREATE DATABASE langfuse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec

-- langfuse 데이터베이스에 대한 권한 부여
GRANT ALL PRIVILEGES ON DATABASE langfuse TO asr;
