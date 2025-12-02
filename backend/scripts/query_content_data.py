"""DB에서 콘텐츠 데이터 조회 스크립트."""
import asyncio
import json
from pathlib import Path
import sys

# 프로젝트 루트를 sys.path에 추가
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.repositories.content_repository import ContentRepository


async def query_content_by_filename(filename: str):
    """파일명으로 콘텐츠 조회."""
    async with AsyncSessionLocal() as session:
        repo = ContentRepository(session)
        
        # 모든 콘텐츠 조회하여 파일명으로 필터링
        all_contents = await repo.list_contents(limit=1000, offset=0)
        
        matching_contents = [c for c in all_contents if filename in c.filename]
        
        if not matching_contents:
            print(f"파일명 '{filename}'과 일치하는 콘텐츠를 찾을 수 없습니다.")
            return
        
        for content in matching_contents:
            print(f"\n{'='*80}")
            print(f"Content ID: {content.id}")
            print(f"Filename: {content.filename}")
            print(f"Object Key: {content.object_key}")
            print(f"Duration: {content.duration_seconds:.2f}초")
            print(f"Speakers: {content.speakers}")
            print(f"Status: {content.status}")
            print(f"Created At: {content.created_at}")
            
            # Transcription 데이터
            transcription = content.transcription
            print(f"\n--- Transcription ---")
            print(f"Text: {transcription.get('text', '')[:200]}...")
            print(f"Language: {transcription.get('language', 'N/A')}")
            print(f"Segments: {len(transcription.get('segments', []))}개")
            
            # Diarization Metadata
            diarization_metadata = transcription.get('diarization_metadata', {})
            if diarization_metadata:
                print(f"\n--- Diarization Metadata ---")
                print(f"Number of Speakers: {diarization_metadata.get('num_speakers', 'N/A')}")
                print(f"Speaker Labels: {diarization_metadata.get('speaker_labels', [])}")
                
                # 화자별 임베딩
                speaker_embeddings = diarization_metadata.get('speaker_embeddings', {})
                if speaker_embeddings:
                    print(f"\n--- Speaker Embeddings ---")
                    for speaker, embedding in speaker_embeddings.items():
                        print(f"  {speaker}: dimension={len(embedding) if isinstance(embedding, list) else 'N/A'}")
                        if isinstance(embedding, list) and len(embedding) > 0:
                            print(f"    Preview: {embedding[:5]}")
                
                # 시간대별 세그먼트 임베딩
                segment_embeddings = diarization_metadata.get('segment_embeddings', [])
                if segment_embeddings:
                    print(f"\n--- Segment Embeddings ({len(segment_embeddings)} segments) ---")
                    for i, seg_emb in enumerate(segment_embeddings[:5]):  # 처음 5개만 표시
                        print(f"  [{i+1}] {seg_emb.get('speaker', 'UNKNOWN')}: "
                              f"{seg_emb.get('start', 0):.2f}s - {seg_emb.get('end', 0):.2f}s "
                              f"({seg_emb.get('duration', 0):.2f}s)")
                        embedding = seg_emb.get('embedding', [])
                        if isinstance(embedding, list):
                            print(f"      Embedding dim: {len(embedding)}, Preview: {embedding[:5]}")
                    if len(segment_embeddings) > 5:
                        print(f"  ... and {len(segment_embeddings) - 5} more segments")
            
            # JSON으로 전체 데이터 출력
            print(f"\n--- Full Transcription JSON (first 2000 chars) ---")
            transcription_json = json.dumps(transcription, ensure_ascii=False, indent=2)
            print(transcription_json[:2000])
            if len(transcription_json) > 2000:
                print(f"... (truncated, total length: {len(transcription_json)} chars)")
            
            print(f"\n{'='*80}")


if __name__ == "__main__":
    filename = "NVIDIA의 새 칩 맥스웰_ 블랙웰.mp4"
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    
    asyncio.run(query_content_by_filename(filename))

