#!/bin/bash
export PYTHONIOENCODING=utf-8
export PYTHONLEGACYWINDOWSSTDIO=utf-8
source rocm_env/Scripts/activate

# 처리 모드 선택 (case1, case2, case3, case4)
# case1: 화자분리 → ASR(전체 파일) 순차 처리
# case2: 화자분리 → ASR(분할 병렬) 순차 처리
# case3: 화자분리와 ASR 모두 분할 병렬 처리
# case4: 화자분리와 ASR(전체 파일) 병렬 처리
PROCESSING_MODE=${1:-case1}

# 모델 및 오디오 파일 설정
MODEL=${2:-large-v3}
AUDIO_FILE=${3:-media/wav/audio_for_whisper_tariff.wav}
# ASR 병렬 조각 수
ASR_CHUNKS=${4:-2}

# 파일명이 경로 없이 주어지면 media/wav/ 기준으로 해석
if [[ "$AUDIO_FILE" != */* && "$AUDIO_FILE" != *\\* ]]; then
  AUDIO_FILE="media/wav/$AUDIO_FILE"
fi

echo "=========================================="
echo "Processing Mode: $PROCESSING_MODE"
echo "Model: $MODEL"
echo "Audio File: $AUDIO_FILE"
echo "ASR Parallel Chunks: $ASR_CHUNKS"
echo "=========================================="

# rocm 기반 ASR만 실행
# python src/asr/test_asr.py $MODEL $AUDIO_FILE  

# 파이프라인 실행 (ASR + 화자분리)
python src/pipeline/test_asr_with_diarization.py $MODEL $AUDIO_FILE $PROCESSING_MODE $ASR_CHUNKS

# 다른 테스트 예시:
# python src/pipeline/test_asr_with_diarization.py large-v3 media/wav/sample.wav case1 2
# python src/pipeline/test_asr_with_diarization.py large-v3 media/wav/audio_for_whisper_tariff.wav case2 3
# python src/pipeline/test_asr_with_diarization.py large-v3 media/wav/audio_for_whisper_tariff.wav case3 4

deactivate

