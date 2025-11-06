# Utils - 유틸리티 모듈

프로젝트에서 사용하는 유틸리티 스크립트들을 관리하는 폴더입니다.

## 파일 목록

### media_converter.py

미디어 파일 변환 스크립트입니다. `media/upload/` 폴더의 미디어 파일을 WAV 형식으로 변환하여 `media/wav/` 폴더에 저장합니다.

**사용법:**
```bash
python src/utils/media_converter.py
```

**지원 형식:**
- `.mp3`, `.mp4`, `.m4a`, `.flac`, `.wav`, `.ogg`, `.wma`, `.aac`, `.mkv`, `.avi`, `.mov`, `.webm`

**요구사항:**
- ffmpeg가 설치되어 있어야 하며 PATH에 포함되어 있어야 합니다.
- Windows: `winget install ffmpeg` 또는 `choco install ffmpeg`

**기본 설정:**
- 샘플 레이트: 16000 Hz
- 채널: 모노 (1채널)
- 입력 폴더: `media/upload/` (프로젝트 루트 기준)
- 출력 폴더: `media/wav/` (프로젝트 루트 기준)

