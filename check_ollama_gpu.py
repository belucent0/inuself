#!/usr/bin/env python3
"""Ollama GPU 사용 여부 확인 스크립트"""
import httpx
import json
import sys

def check_ollama_gpu():
    """Ollama가 GPU를 사용하는지 확인"""
    base_url = "http://localhost:11434"
    
    print("=" * 60)
    print("Ollama GPU 사용 여부 확인")
    print("=" * 60)
    
    # 1. 모델 목록 확인
    print("\n[1] 모델 목록 확인...")
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        print(f"   발견된 모델: {len(models)}개")
        for model in models:
            print(f"   - {model.get('name', 'unknown')}")
    except Exception as e:
        print(f"   ✗ 오류: {e}")
        return
    
    # 2. 모델 정보 확인
    if not models:
        print("   ✗ 모델이 없습니다.")
        return
    
    model_name = models[0].get("name", "")
    print(f"\n[2] 모델 정보 확인: {model_name}")
    try:
        response = httpx.post(
            f"{base_url}/api/show",
            json={"name": model_name},
            timeout=10
        )
        response.raise_for_status()
        model_info = response.json()
        print(f"   파라미터 수: {model_info.get('details', {}).get('parameter_size', 'unknown')}")
        print(f"   양자화: {model_info.get('details', {}).get('quantization_level', 'unknown')}")
    except Exception as e:
        print(f"   ✗ 오류: {e}")
    
    # 3. GPU 사용 테스트 (작은 요청)
    print(f"\n[3] GPU 사용 테스트 (num_gpu=-1)...")
    print("   모델 로드 및 추론 실행 중...")
    try:
        response = httpx.post(
            f"{base_url}/api/generate",
            json={
                "model": model_name,
                "prompt": "Hello",
                "stream": False,
                "options": {
                    "num_predict": 5,
                    "num_gpu": -1,  # 모든 레이어를 GPU에 로드
                },
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        
        print(f"   ✓ 요청 완료")
        print(f"   로드 시간: {result.get('load_duration', 0) / 1e9:.2f}초")
        print(f"   총 시간: {result.get('total_duration', 0) / 1e9:.2f}초")
        print(f"   응답: {result.get('response', '')[:50]}...")
        
        # GPU 사용 여부는 직접 확인할 수 없으므로, 메모리 사용량으로 추정
        print("\n[4] GPU 사용 확인 방법:")
        print("   - 작업 관리자에서 GPU 메모리 사용량 확인")
        print("   - 시스템 RAM 사용량 감소 확인")
        print("   - GPU 사용률 증가 확인")
        
    except Exception as e:
        print(f"   ✗ 오류: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("확인 완료")
    print("=" * 60)

if __name__ == "__main__":
    check_ollama_gpu()






