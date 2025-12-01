#!/usr/bin/env python
"""llama.cpp 호환 모델 다운로드 스크립트"""
import os
import sys
from pathlib import Path

try:
    from huggingface_hub import hf_hub_download
    USE_HF_HUB = True
except ImportError:
    USE_HF_HUB = False
    import requests
    from tqdm import tqdm

def download_file_hf(repo_id: str, filename: str, filepath: Path):
    """huggingface_hub를 사용하여 파일을 다운로드합니다."""
    print(f"huggingface_hub를 사용하여 다운로드 중...")
    downloaded_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=filepath.parent,
        local_dir_use_symlinks=False,
    )
    # 파일을 원하는 위치로 이동
    if downloaded_path != str(filepath):
        import shutil
        shutil.move(downloaded_path, filepath)
    print(f"[OK] 다운로드 완료: {filepath}")
    return filepath

def download_file_url(url: str, filepath: Path, chunk_size: int = 8192):
    """URL에서 파일을 다운로드하고 진행률을 표시합니다."""
    response = requests.get(url, stream=True, allow_redirects=True)
    total_size = int(response.headers.get('content-length', 0))
    
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, 'wb') as f, tqdm(
        desc=filepath.name,
        total=total_size,
        unit='B',
        unit_scale=True,
        unit_divisor=1024,
    ) as pbar:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                pbar.update(len(chunk))
    
    print(f"[OK] 다운로드 완료: {filepath}")
    return filepath

def main():
    """gpt-oss-20b 모델 다운로드"""
    # 프로젝트 루트 찾기
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    models_dir = project_root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print("llama.cpp 모델 다운로드")
    print("=" * 60)
    print(f"모델 저장 경로: {models_dir}")
    print()
    
    # gpt-oss-20b 모델 옵션들
    model_options = {
        "1": {
            "name": "gpt-oss-20b-Q4_K_S.gguf (권장, 작은 크기)",
            "repo_id": "OpenBuddy/gpt-oss-20b-gguf",
            "filename": "gpt-oss-20b-Q4_K_S.gguf",
            "url": "https://huggingface.co/OpenBuddy/gpt-oss-20b-gguf/resolve/main/gpt-oss-20b-Q4_K_S.gguf",
        },
        "2": {
            "name": "gpt-oss-20b-Q4_K_M.gguf (중간 크기, 더 나은 품질)",
            "repo_id": "OpenBuddy/gpt-oss-20b-gguf",
            "filename": "gpt-oss-20b-Q4_K_M.gguf",
            "url": "https://huggingface.co/OpenBuddy/gpt-oss-20b-gguf/resolve/main/gpt-oss-20b-Q4_K_M.gguf",
        },
        "3": {
            "name": "gpt-oss-20b-Q5_K_M.gguf (큰 크기, 최고 품질)",
            "repo_id": "OpenBuddy/gpt-oss-20b-gguf",
            "filename": "gpt-oss-20b-Q5_K_M.gguf",
            "url": "https://huggingface.co/OpenBuddy/gpt-oss-20b-gguf/resolve/main/gpt-oss-20b-Q5_K_M.gguf",
        },
    }
    
    # 명령줄 인자로 모델 선택
    if len(sys.argv) > 1:
        choice = sys.argv[1]
    else:
        print("다운로드할 모델을 선택하세요:")
        for key, option in model_options.items():
            print(f"  {key}. {option['name']}")
        
        try:
            choice = input("\n선택 (1-3, 기본값: 1): ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print("\n기본값(1)을 사용합니다.")
            choice = "1"
    
    if choice not in model_options:
        print(f"잘못된 선택입니다. 기본값(1)을 사용합니다.")
        choice = "1"
    
    selected = model_options[choice]
    model_path = models_dir / selected["filename"]
    
    # 이미 파일이 존재하는지 확인
    if model_path.exists():
        file_size_mb = model_path.stat().st_size / (1024 * 1024)
        print(f"\n[WARNING] 모델 파일이 이미 존재합니다: {model_path}")
        print(f"  크기: {file_size_mb:.2f} MB")
        if len(sys.argv) <= 1:  # 인터랙티브 모드일 때만
            try:
                overwrite = input("덮어쓰시겠습니까? (y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                overwrite = 'n'
        else:
            overwrite = 'y'  # 명령줄 모드에서는 자동으로 덮어쓰기
        if overwrite != 'y':
            print("다운로드를 취소했습니다.")
            return
        model_path.unlink()
    
    print(f"\n다운로드 시작: {selected['name']}")
    print(f"Repository: {selected['repo_id']}")
    print(f"Filename: {selected['filename']}")
    print()
    
    try:
        if USE_HF_HUB:
            download_file_hf(selected["repo_id"], selected["filename"], model_path)
        else:
            print("huggingface_hub가 설치되지 않았습니다. pip install huggingface_hub 권장")
            print("URL 다운로드를 시도합니다...")
            download_file_url(selected["url"], model_path)
        
        # 파일 크기 확인
        file_size_mb = model_path.stat().st_size / (1024 * 1024)
        print(f"\n[OK] 다운로드 완료!")
        print(f"  파일: {model_path}")
        print(f"  크기: {file_size_mb:.2f} MB")
        print(f"\n.env 파일에서 다음 설정을 확인하세요:")
        print(f"  LLM_MODEL_PATH=models/{selected['filename']}")
        
    except Exception as e:
        print(f"\n[ERROR] 다운로드 실패: {e}")
        print("\n대안 방법:")
        print("1. Hugging Face에서 직접 다운로드:")
        print(f"   https://huggingface.co/OpenBuddy/gpt-oss-20b-gguf")
        print(f"2. 파일을 {models_dir}에 저장하세요")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n다운로드가 취소되었습니다.")
        sys.exit(1)

