"""
pyannote.audio 화자 분리 결과에서 추가 메타데이터 추출 예제

이 예제는 다음을 보여줍니다:
1. Embedding 벡터 추출
2. 신뢰도 지표 계산
3. 후처리를 통한 화자 분리 정제
"""

import numpy as np
from worker.diarization_utils import (
    run_diarization,
    extract_speaker_segments,
    extract_speaker_embeddings,
    refine_diarization_with_confidence,
    compute_segment_confidence,
)


def example_basic_extraction(waveform: np.ndarray, sample_rate: int):
    """기본 세그먼트 추출 예제"""
    print("=" * 60)
    print("예제 1: 기본 세그먼트 추출")
    print("=" * 60)
    
    # 화자 분리 실행
    result, load_time, process_time, _ = run_diarization(
        waveform=waveform,
        sample_rate=sample_rate,
        device="cuda",
        return_embeddings=False,
    )
    
    # 기본 세그먼트 추출 (start, end, speaker)
    segments = extract_speaker_segments(result, include_metadata=False)
    print(f"\n추출된 세그먼트 수: {len(segments)}")
    print("처음 5개 세그먼트:")
    for start, end, speaker in segments[:5]:
        print(f"  {start:.2f}s - {end:.2f}s: {speaker}")
    
    # 메타데이터 포함 세그먼트 추출
    segments_with_meta = extract_speaker_segments(result, include_metadata=True)
    print(f"\n메타데이터 포함 세그먼트 (처음 3개):")
    for seg in segments_with_meta[:3]:
        print(f"  {seg}")


def example_with_embeddings(waveform: np.ndarray, sample_rate: int):
    """Embedding 벡터 추출 예제"""
    print("\n" + "=" * 60)
    print("예제 2: Embedding 벡터 추출")
    print("=" * 60)
    
    # Embedding 포함하여 화자 분리 실행
    result, load_time, process_time, embeddings_dict = run_diarization(
        waveform=waveform,
        sample_rate=sample_rate,
        device="cuda",
        return_embeddings=True,
    )
    
    if embeddings_dict:
        print(f"\n추출된 화자 수: {len(embeddings_dict)}")
        for speaker, embedding in embeddings_dict.items():
            print(f"  {speaker}: embedding 차원 = {len(embedding)}")
            print(f"    처음 5개 값: {embedding[:5]}")
    else:
        print("\nEmbedding 추출 실패 또는 지원되지 않음")


def example_confidence_refinement(waveform: np.ndarray, sample_rate: int):
    """신뢰도 기반 정제 예제"""
    print("\n" + "=" * 60)
    print("예제 3: 신뢰도 기반 정제")
    print("=" * 60)
    
    # 화자 분리 실행 (embedding 포함)
    result, load_time, process_time, embeddings_dict = run_diarization(
        waveform=waveform,
        sample_rate=sample_rate,
        device="cuda",
        return_embeddings=True,
    )
    
    # 신뢰도 기반 정제
    refined_segments = refine_diarization_with_confidence(
        result,
        embeddings_dict=embeddings_dict,
        min_confidence=0.3,  # 최소 신뢰도 임계값
    )
    
    print(f"\n원본 세그먼트 수: {len(list(result.itertracks()))}")
    print(f"정제된 세그먼트 수: {len(refined_segments)}")
    
    print("\n정제된 세그먼트 (신뢰도 포함, 처음 5개):")
    for seg in refined_segments[:5]:
        print(
            f"  {seg['start']:.2f}s - {seg['end']:.2f}s: {seg['speaker']} "
            f"(신뢰도: {seg['overall_confidence']:.2f})"
        )


def example_confidence_metrics():
    """신뢰도 지표 설명"""
    print("\n" + "=" * 60)
    print("신뢰도 지표 설명")
    print("=" * 60)
    print("""
각 세그먼트에 대해 다음 신뢰도 지표가 계산됩니다:

1. length_confidence (길이 기반 신뢰도)
   - 세그먼트 길이에 기반
   - 너무 짧은 세그먼트(< 2초)는 신뢰도 낮음
   - 범위: 0.0 ~ 1.0

2. continuity_confidence (연속성 기반 신뢰도)
   - 인접 세그먼트와의 일관성
   - 같은 화자가 연속적으로 나오는지 확인
   - 범위: 0.0 ~ 1.0

3. embedding_confidence (임베딩 기반 신뢰도)
   - 같은 화자의 다른 세그먼트와의 embedding 유사도
   - embedding이 제공된 경우에만 계산
   - 범위: 0.0 ~ 1.0

4. overall_confidence (종합 신뢰도)
   - 위 세 지표의 가중 평균
   - 가중치: length(40%) + continuity(40%) + embedding(20%)
   - 범위: 0.0 ~ 1.0

5. overlap_ratio (겹침 비율)
   - ASR 세그먼트와 화자 세그먼트의 겹침 비율
   - 범위: 0.0 ~ 1.0
    """)


if __name__ == "__main__":
    # 실제 사용 예제
    # import librosa
    # waveform, sample_rate = librosa.load("audio.wav", sr=16000)
    # 
    # example_basic_extraction(waveform, sample_rate)
    # example_with_embeddings(waveform, sample_rate)
    # example_confidence_refinement(waveform, sample_rate)
    # example_confidence_metrics()
    
    print("예제 코드를 실행하려면 위의 주석을 해제하고 오디오 파일 경로를 지정하세요.")



