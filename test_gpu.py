import torch

print("=" * 50)
print("PyTorch GPU 인식 테스트")
print("=" * 50)

print(f"\nPyTorch 버전: {torch.__version__}")
print(f"CUDA/ROCm 사용 가능: {torch.cuda.is_available()}")

if torch.cuda.is_available():
    print(f"GPU 개수: {torch.cuda.device_count()}")
    print(f"현재 GPU: {torch.cuda.get_device_name(0)}")
    print(f"GPU 메모리: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f} GB")
    
    # 실제 연산 테스트
    print("\n" + "-" * 50)
    print("GPU 연산 테스트")
    print("-" * 50)
    
    device = torch.device('cuda')
    print(f"사용 중인 디바이스: {device}")
    
    # 텐서 생성 및 연산
    x = torch.randn(1000, 1000).to(device)
    y = torch.randn(1000, 1000).to(device)
    z = torch.matmul(x, y)
    
    print("✓ GPU에서 행렬 곱셈 성공!")
    print(f"✓ 결과 텐서 위치: {z.device}")
    print(f"✓ 결과 샘플 값: {z[0, 0].item():.6f}")
    
    # 메모리 사용량 확인
    print(f"\nGPU 메모리 할당: {torch.cuda.memory_allocated(0) / 1024**2:.2f} MB")
    print(f"GPU 메모리 예약: {torch.cuda.memory_reserved(0) / 1024**2:.2f} MB")
    
    print("\n" + "=" * 50)
    print("✓ GPU 테스트 성공!")
    print("=" * 50)
else:
    print("\n❌ GPU를 사용할 수 없습니다.")

