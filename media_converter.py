"""
미디어 파일 변환 스크립트
storage 폴더의 미디어 파일(.mp3, .mp4, .m4a, .flac 등)을 wav 형식으로 변환하여 wavs 폴더에 저장합니다.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# 지원하는 미디어 파일 확장자
SUPPORTED_EXTENSIONS = {'.mp3', '.mp4', '.m4a', '.flac', '.wav', '.ogg', '.wma', '.aac', '.mkv', '.avi', '.mov', '.webm'}


def check_ffmpeg() -> bool:
    """ffmpeg가 설치되어 있는지 확인합니다."""
    try:
        subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def convert_to_wav(
    input_file: Path,
    output_file: Path,
    sample_rate: int = 16000,
    channels: int = 1,
    overwrite: bool = False
) -> bool:
    """
    ffmpeg를 사용하여 미디어 파일을 wav 형식으로 변환합니다.
    
    Args:
        input_file: 입력 파일 경로
        output_file: 출력 파일 경로
        sample_rate: 샘플 레이트 (기본값: 16000 Hz)
        channels: 채널 수 (1=모노, 2=스테레오, 기본값: 1)
        overwrite: 기존 파일 덮어쓰기 여부
    
    Returns:
        변환 성공 여부
    """
    if output_file.exists() and not overwrite:
        print(f"⚠️  파일이 이미 존재합니다: {output_file.name} (건너뜀)")
        return False
    
    try:
        # ffmpeg 명령어 구성
        cmd = [
            'ffmpeg',
            '-i', str(input_file),
            '-ar', str(sample_rate),  # 샘플 레이트
            '-ac', str(channels),     # 채널 수
            '-y' if overwrite else '-n',  # 덮어쓰기 옵션
            '-loglevel', 'error',     # 에러만 표시
            str(output_file)
        ]
        
        # 변환 실행
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            file_size = output_file.stat().st_size / (1024 * 1024)  # MB
            print(f"✅ 변환 완료: {input_file.name} → {output_file.name} ({file_size:.2f} MB)")
            return True
        else:
            print(f"❌ 변환 실패: {input_file.name}")
            if result.stderr:
                print(f"   에러: {result.stderr.strip()}")
            return False
            
    except Exception as e:
        print(f"❌ 변환 중 오류 발생: {input_file.name}")
        print(f"   에러: {str(e)}")
        return False


def find_media_files(directory: Path) -> List[Path]:
    """
    디렉토리에서 지원하는 미디어 파일을 찾습니다.
    
    Args:
        directory: 검색할 디렉토리 경로
    
    Returns:
        찾은 미디어 파일 경로 리스트
    """
    media_files = []
    
    if not directory.exists():
        print(f"⚠️  디렉토리가 존재하지 않습니다: {directory}")
        return media_files
    
    for file_path in directory.iterdir():
        if file_path.is_file():
            ext = file_path.suffix.lower()
            if ext in SUPPORTED_EXTENSIONS:
                media_files.append(file_path)
    
    return sorted(media_files)


def get_output_filename(input_file: Path) -> str:
    """
    입력 파일명에서 출력 파일명을 생성합니다.
    
    Args:
        input_file: 입력 파일 경로
    
    Returns:
        출력 파일명 (.wav 확장자)
    """
    return input_file.stem + '.wav'


def convert_storage_to_wavs(
    storage_dir: Path = Path('storage'),
    wavs_dir: Path = Path('wavs'),
    sample_rate: int = 16000,
    channels: int = 1,
    overwrite: bool = False
) -> dict:
    """
    storage 폴더의 미디어 파일을 wavs 폴더로 변환합니다.
    
    Args:
        storage_dir: 입력 미디어 파일이 있는 디렉토리
        wavs_dir: 변환된 wav 파일을 저장할 디렉토리
        sample_rate: 샘플 레이트 (기본값: 16000 Hz)
        channels: 채널 수 (기본값: 1=모노)
        overwrite: 기존 파일 덮어쓰기 여부
    
    Returns:
        변환 결과 통계 딕셔너리
    """
    # 디렉토리 생성
    wavs_dir.mkdir(exist_ok=True)
    
    # 미디어 파일 찾기
    media_files = find_media_files(storage_dir)
    
    if not media_files:
        print(f"📁 {storage_dir} 폴더에서 미디어 파일을 찾을 수 없습니다.")
        return {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0
        }
    
    print(f"📁 {len(media_files)}개의 미디어 파일을 찾았습니다.\n")
    
    # 변환 통계
    stats = {
        'total': len(media_files),
        'success': 0,
        'failed': 0,
        'skipped': 0
    }
    
    # 각 파일 변환
    for i, input_file in enumerate(media_files, 1):
        print(f"[{i}/{len(media_files)}] 처리 중: {input_file.name}")
        
        output_filename = get_output_filename(input_file)
        output_file = wavs_dir / output_filename
        
        if output_file.exists() and not overwrite:
            stats['skipped'] += 1
            print(f"⚠️  파일이 이미 존재합니다: {output_filename} (건너뜀)\n")
            continue
        
        success = convert_to_wav(
            input_file=input_file,
            output_file=output_file,
            sample_rate=sample_rate,
            channels=channels,
            overwrite=overwrite
        )
        
        if success:
            stats['success'] += 1
        else:
            stats['failed'] += 1
        
        print()  # 빈 줄
    
    # 결과 요약
    print("=" * 50)
    print("변환 완료!")
    print(f"  전체: {stats['total']}개")
    print(f"  성공: {stats['success']}개")
    print(f"  실패: {stats['failed']}개")
    print(f"  건너뜀: {stats['skipped']}개")
    print("=" * 50)
    
    return stats


def main():
    """메인 함수"""
    print("=" * 50)
    print("미디어 파일 변환 스크립트")
    print("=" * 50)
    print()
    
    # ffmpeg 확인
    if not check_ffmpeg():
        print("❌ ffmpeg가 설치되어 있지 않습니다.")
        print()
        print("ffmpeg 설치 방법:")
        print("  Windows: https://ffmpeg.org/download.html 에서 다운로드")
        print("  또는: winget install ffmpeg")
        print("  또는: choco install ffmpeg")
        print()
        print("설치 후 PATH 환경변수에 ffmpeg가 포함되어 있는지 확인하세요.")
        sys.exit(1)
    
    print("✅ ffmpeg가 설치되어 있습니다.")
    print()
    
    # 경로 설정
    storage_dir = Path('storage')
    wavs_dir = Path('wavs')
    
    # 변환 실행
    stats = convert_storage_to_wavs(
        storage_dir=storage_dir,
        wavs_dir=wavs_dir,
        sample_rate=16000,
        channels=1,
        overwrite=False
    )
    
    if stats['failed'] > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()

