# whisper/check_result.py
"""
전사 완료 여부를 확인하고 결과를 출력하는 스크립트
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime

def find_latest_result(audio_name=None, model_name=None):
    """가장 최근 전사 결과 파일 찾기"""
    whisper_dir = Path(__file__).parent
    logs_dir = whisper_dir / "logs"
    
    if not logs_dir.exists():
        return None
    
    # 조건에 맞는 파일 찾기
    pattern = "asr_*.json"
    files = sorted(logs_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    
    if not files:
        return None
    
    # 필터링
    if audio_name:
        files = [f for f in files if audio_name in f.name]
    if model_name:
        files = [f for f in files if model_name in f.name]
    
    return files[0] if files else None

def print_result(result_file):
    """결과 파일을 읽어서 출력"""
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    print(f"\n{'='*60}")
    print("📋 전사 결과")
    print(f"{'='*60}")
    print(f"모델: {data['model']}")
    print(f"오디오 파일: {Path(data['audio_file']).name}")
    print(f"오디오 길이: {data['audio_duration']:.2f}초")
    print(f"처리 시간: {data['processing_time']:.2f}초")
    print(f"속도: {data['speed_x']:.2f}x 실시간")
    print(f"GPU 메모리: {data['gpu_memory_gb']:.2f} GB")
    print(f"처리 시간: {data['timestamp']}")
    print(f"\n전사 텍스트:")
    print("-" * 60)
    print(data['result']['text'])
    print("-" * 60)
    
    if data['result']['segments']:
        print(f"\n세그먼트 ({len(data['result']['segments'])}개):")
        for seg in data['result']['segments']:
            print(f"  [{seg['start']:.2f}s - {seg['end']:.2f}s] {seg['text']}")
    
    print(f"\n결과 파일: {result_file}")
    print(f"{'='*60}")

if __name__ == "__main__":
    # 명령줄 인자 처리
    audio_name = sys.argv[1] if len(sys.argv) > 1 else None
    model_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    result_file = find_latest_result(audio_name, model_name)
    
    if result_file:
        print_result(result_file)
    else:
        print("❌ 전사 결과 파일을 찾을 수 없습니다.")
        print(f"검색 위치: {Path(__file__).parent / 'logs'}")
        sys.exit(1)

