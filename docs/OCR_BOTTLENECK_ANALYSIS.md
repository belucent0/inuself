# OCR PDF 처리 병목 지점 분석

## PDF OCR 처리 흐름

```
1. 파일 다운로드 (스토리지)
   ↓
2. PDF → 이미지 변환 (PyMuPDF/pdf2image)
   ↓
3. 각 페이지 순차 처리:
   ├─ 이미지 리사이즈 (1536x1536)
   ├─ JPEG 압축 (quality=75)
   ├─ base64 인코딩
   ├─ HTTP API 호출 (llama-server)
   ├─ Vision Encoder 처리 (GPU)
   └─ 텍스트 추출
   ↓
4. 결과 저장 (DB)
```

## 병목 지점 분석

### 🔴 1. Vision Encoder 처리 (가장 큰 병목) ⚠️

**위치**: `llama-server` 내부 (Vision 모델 처리)

**특징:**
- **처리 시간**: 페이지당 약 5-30초 (이미지 크기와 복잡도에 따라)
- **GPU 사용률**: 100% (처리 중)
- **메모리 사용**: Vision 모델(mmproj) ~2GB + 이미지 버퍼
- **병렬 처리**: 불가능 (순차 처리)

**원인:**
- Vision Encoder가 이미지를 토큰으로 변환하는 과정이 GPU 집약적
- Qwen3-VL-30B 모델의 Vision Encoder는 큰 이미지를 처리하는 데 시간이 오래 걸림
- 각 페이지를 순차적으로 처리하므로 전체 시간 = 페이지 수 × 페이지당 처리 시간

**코드 위치:**
```python
# backend/app/services/ocr_service.py
page_text = self._call_llm_api(prompt, image_base64, server_process=server_process)
# → llama-server의 Vision Encoder가 처리
```

**개선 방안:**
- 이미지 크기 최적화 (1536x1536으로 제한) ✅ 이미 적용됨
- JPEG 품질 조정 (75로 설정) ✅ 이미 적용됨
- 페이지 병렬 처리 (현재 불가능 - llama-server가 순차 처리)

---

### 🟡 2. PDF → 이미지 변환 (중간 병목)

**위치**: `_pdf_to_images_pymupdf()` 또는 `_pdf_to_images_pdf2image()`

**특징:**
- **처리 시간**: 페이지당 약 0.5-2초 (PDF 복잡도에 따라)
- **CPU 사용률**: 높음 (렌더링 작업)
- **메모리 사용**: 각 페이지 이미지가 메모리에 로드됨
- **병렬 처리**: 가능 (하지만 현재 순차 처리)

**원인:**
- PDF 렌더링은 CPU 집약적 작업
- DPI가 높을수록 처리 시간 증가 (200 DPI 사용 중)
- 복잡한 PDF (많은 그래픽, 폰트)는 더 오래 걸림

**코드 위치:**
```python
# backend/app/services/ocr_service.py
images = self._pdf_to_images(file_path)  # 모든 페이지를 한 번에 변환
```

**개선 방안:**
- DPI 최적화 (200 DPI 사용 중) ✅ 이미 적용됨
- 페이지별 지연 로딩 (현재는 모든 페이지를 한 번에 변환)
- 병렬 변환 (현재는 순차 변환)

---

### 🟡 3. 이미지 리사이즈 및 base64 인코딩 (작은 병목)

**위치**: `_image_to_base64()`

**특징:**
- **처리 시간**: 페이지당 약 0.1-0.5초
- **CPU 사용률**: 중간 (이미지 처리)
- **메모리 사용**: PIL Image + base64 데이터 중복

**원인:**
- 이미지 리사이즈 (LANCZOS 리샘플링)
- JPEG 압축
- base64 인코딩

**코드 위치:**
```python
# backend/app/services/ocr_service.py
image_base64 = self._image_to_base64(image, max_size=(1536, 1536), quality=75)
```

**개선 방안:**
- 이미지 크기 제한 (1536x1536) ✅ 이미 적용됨
- JPEG 품질 조정 (75) ✅ 이미 적용됨
- 메모리 정리 강화 ✅ 이미 적용됨

---

### 🟢 4. HTTP API 호출 (네트워크 오버헤드)

**위치**: `_call_llm_api()`

**특징:**
- **처리 시간**: 약 0.1-0.5초 (네트워크 지연)
- **네트워크**: localhost이므로 매우 빠름
- **대기 시간**: Vision Encoder 처리 시간에 포함됨

**원인:**
- HTTP 요청/응답 오버헤드
- base64 이미지 데이터 전송 (압축 후 크기 감소)

**코드 위치:**
```python
# backend/app/services/ocr_service.py
response = client.post(url, json=payload)  # 5분 타임아웃
```

**개선 방안:**
- localhost 사용 중이므로 네트워크 병목 없음 ✅
- 타임아웃 설정 적절 (5분) ✅

---

### 🟢 5. 파일 다운로드 (최소 병목)

**위치**: `download_file()`

**특징:**
- **처리 시간**: 파일 크기에 비례 (일반적으로 1-5초)
- **네트워크/디스크**: I/O 작업
- **병목 영향**: 낮음 (한 번만 수행)

**개선 방안:**
- 이미 최적화됨 ✅

---

## 시간 분해 (예시: 10페이지 PDF)

### 전체 처리 시간: 약 60-300초 (페이지당 6-30초)

1. **파일 다운로드**: ~2초 (2%)
2. **PDF → 이미지 변환**: ~10초 (10%)
   - 10페이지 × 1초/페이지
3. **각 페이지 OCR 처리**: ~240초 (80%) ⚠️ **가장 큰 병목**
   - 10페이지 × 24초/페이지
   - 이미지 리사이즈/인코딩: ~2초 (0.8%)
   - Vision Encoder 처리: ~230초 (96%) ⚠️ **핵심 병목**
   - HTTP 오버헤드: ~8초 (3.2%)
4. **결과 저장**: ~3초 (1%)

---

## 병목 지점 우선순위

### 🔴 최우선 (Critical) - Vision Encoder 처리

**비중**: 전체 시간의 **80-90%**

**특징:**
- GPU 집약적 작업
- 순차 처리 (병렬화 어려움)
- 이미지 크기에 매우 민감

**개선 방안:**
1. ✅ 이미지 크기 최적화 (1536x1536)
2. ✅ JPEG 품질 조정 (75)
3. ⚠️ 페이지 병렬 처리 (llama-server 제한으로 어려움)
4. ⚠️ 더 작은 Vision 모델 사용 (품질 저하 가능)

---

### 🟡 중간 우선순위 - PDF → 이미지 변환

**비중**: 전체 시간의 **5-15%**

**특징:**
- CPU 집약적 작업
- 병렬 처리 가능

**개선 방안:**
1. ✅ DPI 최적화 (200 DPI)
2. ⚠️ 페이지별 지연 로딩 (현재는 모든 페이지를 한 번에 변환)
3. ⚠️ 병렬 변환 (멀티프로세싱)

---

### 🟢 낮은 우선순위 - 기타

**비중**: 전체 시간의 **5% 미만**

- 이미지 리사이즈/인코딩: 이미 최적화됨
- HTTP 오버헤드: localhost이므로 무시 가능
- 파일 다운로드: 한 번만 수행되므로 영향 적음

---

## 실제 병목 확인 방법

### 로그 분석

OCR 처리 로그를 확인하여 각 단계별 시간을 측정:

```python
# backend/app/services/ocr_service.py에 시간 측정 추가
import time

start_time = time.time()
images = self._pdf_to_images(file_path)  # PDF → 이미지 변환
pdf_conversion_time = time.time() - start_time
logger.info(f"PDF conversion time: {pdf_conversion_time:.2f}s")

for page_idx, image in enumerate(images):
    page_start = time.time()
    
    # 이미지 처리
    image_base64 = self._image_to_base64(image, ...)
    image_processing_time = time.time() - page_start
    
    # Vision Encoder 처리
    api_start = time.time()
    page_text = self._call_llm_api(prompt, image_base64, ...)
    vision_processing_time = time.time() - api_start
    
    logger.info(
        f"Page {page_idx + 1}: "
        f"image={image_processing_time:.2f}s, "
        f"vision={vision_processing_time:.2f}s"
    )
```

### 프로파일링

Python 프로파일러를 사용하여 각 함수의 실행 시간 측정:

```python
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()

ocr_result = ocr_service.process_document(temp_path)

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(20)  # 상위 20개 함수
```

---

## 결론

**PDF OCR 처리의 주요 병목은 Vision Encoder 처리입니다.**

- 전체 처리 시간의 **80-90%**를 차지
- GPU 집약적 작업으로 병렬화가 어려움
- 이미지 크기 최적화로 일부 개선 가능 (이미 적용됨)

**추가 최적화 여지:**
1. 페이지별 지연 로딩 (메모리 사용량 감소)
2. 더 작은 Vision 모델 사용 (품질 vs 속도 트레이드오프)
3. llama-server의 배치 처리 지원 (현재는 순차 처리)

