# OCR 처리 성능 최적화 가이드

## OCR 처리 시 버벅거림 현상 원인

OCR 처리 시 ASR이나 LLM 요약과 달리 컴퓨터가 버벅거리는 주요 원인:

### 1. Vision 모델의 이미지 처리 부하

**OCR의 특수성:**
- **ASR**: 오디오 → 텍스트 변환 (이미지 처리 없음)
- **LLM 요약**: 텍스트만 처리 (이미지 처리 없음)
- **OCR**: 이미지 → Vision Encoder → 텍스트 (추가 처리 단계)

**Vision Encoder 처리 과정:**
1. 이미지를 Vision Encoder로 처리하여 이미지 토큰으로 변환
2. 이미지 토큰과 텍스트 토큰을 결합하여 처리
3. 이 과정에서 추가적인 GPU/CPU 연산이 발생

**메모리 사용:**
- Vision 모델(mmproj): ~2GB (Qwen3-VL-30B 기준)
- 이미지 처리 버퍼: 이미지 크기에 비례
- 이미지 토큰 캐시: 처리 중 메모리 사용

### 2. 이미지 데이터의 메모리 중복

OCR 처리 시 메모리에 동시에 존재하는 데이터:
- PIL Image 객체 (원본 이미지)
- base64 인코딩된 이미지 데이터
- Vision Encoder 처리 중 이미지 버퍼

### 3. 이미지 크기 영향

이미지가 클수록:
- 메모리 사용량 증가 (픽셀 수에 비례)
- Vision Encoder 처리 시간 증가
- GPU/CPU 부하 증가

**예시:**
- 2048x2048 이미지: ~12MB (RGB, 압축 전)
- 1536x1536 이미지: ~7MB (RGB, 압축 전)
- 1280x1280 이미지: ~5MB (RGB, 압축 전)

## 최적화 방법

### 1. 이미지 크기 제한 (가장 효과적)

**변경 사항:**
- 최대 이미지 크기: 2048x2048 → **1536x1536**
- JPEG 품질: 85 → **75**

**효과:**
- 메모리 사용량 약 30-40% 감소
- Vision Encoder 처리 시간 약 20-30% 감소
- base64 인코딩 데이터 크기 감소

**코드 위치:**
```python
# backend/app/services/ocr_service.py
image_base64 = self._image_to_base64(image, max_size=(1536, 1536), quality=75)
```

### 2. PDF DPI 최적화

**변경 사항:**
- PDF → 이미지 변환 DPI: **200 DPI** (OCR 품질과 성능의 균형점)

**DPI 선택 가이드:**
- **150 DPI**: 성능 우선 (작은 텍스트 인식 정확도 약간 저하 가능)
- **200 DPI**: 권장 (품질과 성능의 좋은 균형) ⭐
- **250-300 DPI**: 고품질 (메모리/처리 부담 증가, 작은 텍스트나 복잡한 문서에 적합)

**효과:**
- 200 DPI는 OCR 품질을 유지하면서도 메모리 사용량을 적절히 제어
- 일반 문서에서 충분한 인식 정확도 제공
- Vision 모델(Qwen3-VL)의 성능을 최대한 활용

**코드 위치:**
```python
# backend/app/services/ocr_service.py
def _pdf_to_images_pymupdf(self, pdf_path: Path, max_dpi: int = 200):
    # ...
```

### 3. 메모리 정리 강화

**변경 사항:**
- 각 페이지 처리 후 명시적 가비지 컬렉션 추가
- 이미지 객체 즉시 해제

**효과:**
- 메모리 누수 방지
- 처리 중 메모리 사용량 안정화

**코드 위치:**
```python
# backend/app/services/ocr_service.py
finally:
    image.close()
    image_base64 = None
    gc.collect()  # 명시적 가비지 컬렉션
```

## 성능 비교

### 최적화 전
- 이미지 크기: 최대 2048x2048
- JPEG 품질: 85
- PDF DPI: 300 (또는 기본값)
- 메모리 사용: 높음
- 처리 부하: 높음

### 최적화 후
- 이미지 크기: 최대 1536x1536
- JPEG 품질: 75
- PDF DPI: 200 (OCR 품질과 성능의 균형)
- 메모리 사용: 약 30-40% 감소
- 처리 부하: 약 20-30% 감소
- OCR 품질: 유지 (200 DPI는 충분한 인식 정확도 제공)

## 추가 최적화 옵션

### 더 공격적인 최적화 (품질 약간 저하)

이미지 크기를 더 줄이려면:

```python
# backend/app/services/ocr_service.py
image_base64 = self._image_to_base64(image, max_size=(1280, 1280), quality=70)
```

**효과:**
- 메모리 사용량 추가 20-30% 감소
- 처리 시간 추가 15-20% 감소
- OCR 정확도 약간 저하 가능

### PDF DPI 조정

**성능 우선 (품질 약간 저하):**
```python
# backend/app/services/ocr_service.py
def _pdf_to_images_pymupdf(self, pdf_path: Path, max_dpi: int = 150):
    # ...
```

**고품질 (메모리 부담 증가):**
```python
# backend/app/services/ocr_service.py
def _pdf_to_images_pymupdf(self, pdf_path: Path, max_dpi: int = 250):
    # ...
```

**효과:**
- 150 DPI: 메모리 사용량 추가 감소, 작은 텍스트 인식 정확도 약간 저하 가능
- 250-300 DPI: 최고 품질, 작은 텍스트나 복잡한 문서에 적합, 메모리/처리 부담 증가

## 권장 설정

### 일반적인 사용 (권장) ⭐
- 이미지 크기: 1536x1536
- JPEG 품질: 75
- PDF DPI: 200

### 메모리가 부족한 경우
- 이미지 크기: 1280x1280
- JPEG 품질: 70
- PDF DPI: 150

### 최고 품질이 필요한 경우
- 이미지 크기: 1536x1536 (또는 2048x2048)
- JPEG 품질: 80-85
- PDF DPI: 250-300 (작은 텍스트나 복잡한 문서)

## 모니터링

OCR 처리 중 시스템 리소스 모니터링:

1. **작업 관리자** (Windows):
   - 성능 탭 → GPU: Vision Encoder 처리 부하 확인
   - 성능 탭 → 메모리: 이미지 데이터 메모리 사용 확인

2. **로그 확인**:
   ```
   [OCR] Processing page 1/5
   Image resized from (3000, 4000) to (1536, 2048) (max_size=(1536, 1536))
   Page 1 OCR completed (1234 chars)
   ```

## 참고 사항

- Vision 모델의 이미지 처리 부하는 모델 자체의 특성으로 완전히 제거할 수 없습니다
- 이미지 크기를 줄이면 OCR 정확도가 약간 저하될 수 있지만, 일반적으로 1536x1536 크기에서는 큰 차이가 없습니다
- JPEG 품질을 너무 낮추면(70 이하) 텍스트 인식 정확도가 저하될 수 있습니다

