import uvicorn
import os

def start():
    """
    Run uvicorn server with reload enabled.
    Entry point for 'poetry run dev'.
    """
    # 프로젝트 루트가 PYTHONPATH에 포함되도록 설정 (필요한 경우)
    # 하지만 보통 poetry run 시에는 이미 설정됨
    
    # uvicorn 실행
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    start()
