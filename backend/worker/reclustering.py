"""화자 재클러스터링 유틸리티 (torch 의존성 없음)."""
from typing import Any
import numpy as np


def recluster_speakers_from_embeddings(
    segment_embeddings: list[dict[str, Any]],
    target_num_speakers: int | None = None,
    similarity_threshold: float = 0.7,
) -> dict[int, str]:
    """
    세그먼트 임베딩을 기반으로 코사인 유사도를 사용하여 화자를 재클러스터링합니다.
    
    Args:
        segment_embeddings: 시간대별 세그먼트 임베딩 리스트
            각 항목은 {"start": float, "end": float, "speaker": str, "embedding": list[float]} 형태
        target_num_speakers: 목표 화자 수 (None이면 자동 결정)
        similarity_threshold: 코사인 유사도 임계값 (0.0 ~ 1.0)
    
    Returns:
        {segment_index: new_speaker_label} 형태의 딕셔너리
    """
    try:
        from scipy.spatial.distance import cosine
        
        if not segment_embeddings:
            return {}
        
        num_segments = len(segment_embeddings)
        print(f"[Reclustering] Starting reclustering for {num_segments} segments")
        print(f"[Reclustering] Target speakers: {target_num_speakers}, Similarity threshold: {similarity_threshold}")
        
        # 임베딩 벡터 추출
        embeddings = []
        for seg in segment_embeddings:
            emb = np.array(seg['embedding'])
            embeddings.append(emb)
        
        embeddings = np.array(embeddings)
        print(f"[Reclustering] Embeddings shape: {embeddings.shape}")
        
        # 코사인 유사도 행렬 계산
        similarity_matrix = np.zeros((num_segments, num_segments))
        for i in range(num_segments):
            for j in range(i + 1, num_segments):
                similarity = 1 - cosine(embeddings[i], embeddings[j])
                similarity_matrix[i, j] = similarity
                similarity_matrix[j, i] = similarity
        
        # 초기 그룹 생성: 유사도가 임계값 이상인 세그먼트들을 같은 그룹으로 묶음
        groups = []
        assigned = set()
        
        for i in range(num_segments):
            if i in assigned:
                continue
            
            # 새 그룹 시작
            current_group = [i]
            assigned.add(i)
            
            # 유사한 세그먼트 찾기
            for j in range(i + 1, num_segments):
                if j in assigned:
                    continue
                if similarity_matrix[i, j] >= similarity_threshold:
                    current_group.append(j)
                    assigned.add(j)
            
            groups.append(current_group)
        
        print(f"[Reclustering] Initial groups: {len(groups)}")
        
        # 목표 화자 수에 맞춰 그룹 조정
        if target_num_speakers is not None:
            current_num_groups = len(groups)
            
            if current_num_groups > target_num_speakers:
                # 그룹 수가 많으면 병합 필요
                print(f"[Reclustering] Merging {current_num_groups} groups to {target_num_speakers}")
                
                # 그룹 간 평균 유사도 계산
                group_embeddings = []
                for group in groups:
                    group_emb = np.mean([embeddings[idx] for idx in group], axis=0)
                    group_embeddings.append(group_emb)
                
                # 그룹 간 유사도 행렬
                group_similarity_matrix = np.zeros((len(groups), len(groups)))
                for i in range(len(groups)):
                    for j in range(i + 1, len(groups)):
                        similarity = 1 - cosine(group_embeddings[i], group_embeddings[j])
                        group_similarity_matrix[i, j] = similarity
                        group_similarity_matrix[j, i] = similarity
                
                # 가장 유사한 그룹부터 병합
                while len(groups) > target_num_speakers:
                    # 가장 유사한 두 그룹 찾기
                    max_similarity = -1
                    merge_i, merge_j = -1, -1
                    
                    for i in range(len(groups)):
                        for j in range(i + 1, len(groups)):
                            if group_similarity_matrix[i, j] > max_similarity:
                                max_similarity = group_similarity_matrix[i, j]
                                merge_i, merge_j = i, j
                    
                    if merge_i == -1:
                        break
                    
                    # 그룹 병합
                    groups[merge_i].extend(groups[merge_j])
                    groups.pop(merge_j)
                    
                    # 그룹 임베딩 재계산
                    group_embeddings = []
                    for group in groups:
                        group_emb = np.mean([embeddings[idx] for idx in group], axis=0)
                        group_embeddings.append(group_emb)
                    
                    # 유사도 행렬 재계산
                    group_similarity_matrix = np.zeros((len(groups), len(groups)))
                    for i in range(len(groups)):
                        for j in range(i + 1, len(groups)):
                            similarity = 1 - cosine(group_embeddings[i], group_embeddings[j])
                            group_similarity_matrix[i, j] = similarity
                            group_similarity_matrix[j, i] = similarity
                
            elif current_num_groups < target_num_speakers:
                # 그룹 수가 적으면 분리 필요
                print(f"[Reclustering] Splitting groups from {current_num_groups} to {target_num_speakers}")
                
                # 가장 큰 그룹부터 분리
                while len(groups) < target_num_speakers:
                    # 가장 큰 그룹 찾기
                    largest_group_idx = max(range(len(groups)), key=lambda i: len(groups[i]))
                    largest_group = groups[largest_group_idx]
                    
                    if len(largest_group) < 2:
                        break
                    
                    # 그룹 내 세그먼트 간 유사도 계산
                    group_embeddings_list = [embeddings[idx] for idx in largest_group]
                    
                    # 가장 유사도가 낮은 두 세그먼트 찾기
                    min_similarity = float('inf')
                    split_idx1, split_idx2 = -1, -1
                    
                    for i in range(len(largest_group)):
                        for j in range(i + 1, len(largest_group)):
                            seg_i = largest_group[i]
                            seg_j = largest_group[j]
                            similarity = similarity_matrix[seg_i, seg_j]
                            if similarity < min_similarity:
                                min_similarity = similarity
                                split_idx1, split_idx2 = i, j
                    
                    if split_idx1 == -1:
                        break
                    
                    # 두 세그먼트를 기준으로 그룹 분리
                    seg1_idx = largest_group[split_idx1]
                    seg2_idx = largest_group[split_idx2]
                    
                    group1 = [seg1_idx]
                    group2 = [seg2_idx]
                    
                    for idx in largest_group:
                        if idx == seg1_idx or idx == seg2_idx:
                            continue
                        sim1 = similarity_matrix[idx, seg1_idx]
                        sim2 = similarity_matrix[idx, seg2_idx]
                        if sim1 > sim2:
                            group1.append(idx)
                        else:
                            group2.append(idx)
                    
                    # 원래 그룹을 두 그룹으로 교체
                    groups.pop(largest_group_idx)
                    groups.append(group1)
                    groups.append(group2)
        
        # 새로운 화자 라벨 생성 및 매핑
        new_labels = [f"SPEAKER_{i:02d}" for i in range(len(groups))]
        segment_to_speaker = {}
        
        for group_idx, group in enumerate(groups):
            speaker_label = new_labels[group_idx]
            for seg_idx in group:
                segment_to_speaker[seg_idx] = speaker_label
        
        print(f"[Reclustering] Final groups: {len(groups)}")
        print(f"[Reclustering] New speaker labels: {new_labels}")
        
        return segment_to_speaker
    
    except ImportError:
        print("[Reclustering] scipy not available, cannot perform reclustering")
        raise ImportError("scipy is required for reclustering. Install it with: pip install scipy")
    except Exception as e:
        print(f"[Reclustering] Error in reclustering: {e}")
        import traceback
        traceback.print_exc()
        raise


def update_transcription_with_new_speakers(
    transcription: dict[str, Any],
    segment_to_speaker_mapping: dict[int, str],
    segment_embeddings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    transcription을 새로운 화자 라벨로 업데이트합니다.
    
    Args:
        transcription: 기존 transcription 딕셔너리
        segment_to_speaker_mapping: {segment_index: new_speaker_label} 매핑
        segment_embeddings: 시간대별 세그먼트 임베딩 리스트
    
    Returns:
        업데이트된 transcription 딕셔너리
    """
    import copy
    
    updated_transcription = copy.deepcopy(transcription)
    
    # 새로운 화자 라벨 리스트 생성
    new_speaker_labels = sorted(set(segment_to_speaker_mapping.values()))
    num_speakers = len(new_speaker_labels)
    
    print(f"[Update] Updating transcription with {num_speakers} speakers: {new_speaker_labels}")
    
    # 1. segments의 speaker 필드 업데이트
    if 'segments' in updated_transcription:
        for seg_idx, segment in enumerate(updated_transcription['segments']):
            if seg_idx in segment_to_speaker_mapping:
                segment['speaker'] = segment_to_speaker_mapping[seg_idx]
    
    # 2. diarization_metadata 업데이트
    if 'diarization_metadata' not in updated_transcription:
        updated_transcription['diarization_metadata'] = {}
    
    metadata = updated_transcription['diarization_metadata']
    
    # 3. segment_embeddings의 speaker 필드 업데이트
    if 'segment_embeddings' in metadata:
        updated_segment_embeddings = []
        for seg_idx, seg_emb in enumerate(metadata['segment_embeddings']):
            if seg_idx in segment_to_speaker_mapping:
                updated_seg_emb = copy.deepcopy(seg_emb)
                updated_seg_emb['speaker'] = segment_to_speaker_mapping[seg_idx]
                updated_segment_embeddings.append(updated_seg_emb)
            else:
                updated_segment_embeddings.append(seg_emb)
        metadata['segment_embeddings'] = updated_segment_embeddings
    
    # 4. speaker_labels 업데이트
    metadata['speaker_labels'] = new_speaker_labels
    
    # 5. num_speakers 업데이트
    metadata['num_speakers'] = num_speakers
    
    # 6. speaker_embeddings 재계산 (각 새 화자의 대표 임베딩)
    speaker_embeddings = {}
    for speaker_label in new_speaker_labels:
        # 해당 화자의 모든 세그먼트 임베딩 수집
        speaker_segment_indices = [
            idx for idx, label in segment_to_speaker_mapping.items()
            if label == speaker_label
        ]
        
        if speaker_segment_indices:
            # 가장 긴 세그먼트의 임베딩을 대표로 사용
            speaker_segments = [
                (idx, seg_emb) for idx, seg_emb in enumerate(segment_embeddings)
                if idx in speaker_segment_indices
            ]
            
            if speaker_segments:
                # 가장 긴 세그먼트 선택
                longest_seg = max(speaker_segments, key=lambda x: x[1]['duration'])
                speaker_embeddings[speaker_label] = longest_seg[1]['embedding']
    
    metadata['speaker_embeddings'] = speaker_embeddings
    
    print(f"[Update] Updated {len(segment_to_speaker_mapping)} segments")
    print(f"[Update] New speaker embeddings: {list(speaker_embeddings.keys())}")
    
    return updated_transcription



