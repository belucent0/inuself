import httpx
import subprocess
import tempfile
import os
import asyncio
import json
import re
from functools import lru_cache
from typing import Optional
from fastapi import WebSocket
from openai import AsyncOpenAI
from ..core.logging import logger
from ..core.llm_tier import LLMTier


def get_litellm_base_url() -> str:
    """LiteLLM 프록시 URL 반환."""
    if litellm_url := os.getenv("LITELLM_BASE_URL"):
        return litellm_url
    return "http://localhost:4000"


def get_litellm_api_key() -> str:
    """LiteLLM API 키 반환."""
    return os.getenv("LITELLM_API_KEY", "")


@lru_cache(maxsize=1)
def get_async_openai_client() -> AsyncOpenAI:
    """AsyncOpenAI 클라이언트 싱글톤."""
    base_url = get_litellm_base_url().rstrip("/")
    return AsyncOpenAI(
        base_url=f"{base_url}/v1",
        api_key=get_litellm_api_key(),
        timeout=30.0,
    )


def get_audio_rms(wav_bytes: bytes) -> int:
    """WAV 바이트 데이터의 전체 RMS를 계산."""
    import audioop
    try:
        # WAV 헤더(44바이트) 건너뛰기
        if len(wav_bytes) < 44:
            return 0
            
        # raw PCM data (16-bit mono)
        pcm_data = wav_bytes[44:]
        if not pcm_data:
            return 0
             
        # RMS 계산 (width=2 for 16-bit)
        rms = audioop.rms(pcm_data, 2)
        return rms
    except Exception:
        return 0

def convert_webm_to_wav(webm_data: bytes) -> Optional[bytes]:
    """WebM(Opus) 데이터를 16kHz Mono WAV로 변환."""
    try:
        # 임시 파일 생성
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp_webm:
            temp_webm.write(webm_data)
            temp_webm_path = temp_webm.name
            
        temp_wav_path = temp_webm_path.replace(".webm", ".wav")
        
        # ffmpeg 변환
        # -y: 덮어쓰기
        # -i: 입력
        # -ar 16000: 샘플링 레이트 16kHz
        # -ac 1: 채널 1 (Mono)
        # -c:a pcm_s16le: 16비트 PCM
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", temp_webm_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            temp_wav_path
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if os.path.exists(temp_wav_path):
            with open(temp_wav_path, "rb") as f:
                wav_data = f.read()
            
            # 정리
            os.remove(temp_wav_path)
            os.remove(temp_webm_path)
            return wav_data
            
        os.remove(temp_webm_path)
        return None
        
    except Exception as e:
        logger.error(f"[Convert] Error converting audio: {e}")
        # 파일 정리 시도
        if 'temp_webm_path' in locals() and os.path.exists(temp_webm_path):
            os.remove(temp_webm_path)
        if 'temp_wav_path' in locals() and os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)
        return None

def is_silence(wav_bytes: bytes, threshold: int = 500, check_duration_sec: float = 1.0) -> bool:
    """WAV 바이트 데이터의 RMS를 계산하여 침묵 여부 판단."""
    import audioop
    try:
        # WAV 헤더(44바이트) 건너뛰기
        if len(wav_bytes) < 44:
            return True
            
        # raw PCM data (16-bit mono)
        pcm_data = wav_bytes[44:]
        if not pcm_data:
            return True
            
        # 마지막 부분만 체크 (최신 입력에 대한 반응성 확보)
        sample_rate = 16000
        bytes_per_sample = 2
        check_bytes = int(sample_rate * bytes_per_sample * check_duration_sec)
        
        if len(pcm_data) > check_bytes:
             pcm_data = pcm_data[-check_bytes:]
             
        # RMS 계산 (width=2 for 16-bit)
        rms = audioop.rms(pcm_data, 2)
        
        logger.debug(f"[VAD] Audio RMS (last {check_duration_sec}s): {rms}") 
        return rms < threshold
    except Exception as e:
        logger.error(f"VAD Error: {e}")
        return False

async def process_llm_background(websocket: WebSocket, text: str, segment_id: str, base_url: str):
    """
    백그라운드에서 LLM 후처리를 수행하고 결과를 웹소켓으로 전송합니다.
    """
    try:
        processed_text = await post_process_with_llm(text, base_url)
        
        # 교정된 내용이 있고 원본과 다를 경우에만 전송
        if processed_text and processed_text != text:
            # 웹소켓 연결 상태 확인 (try-except로 처리)
            await websocket.send_json({
                "type": "correction",
                "segment_id": segment_id,
                "original_text": text,
                "processed_text": processed_text
            })
            logger.info(f"[LLM Background] Sent correction for {segment_id}")
    except Exception as e:
        logger.error(f"[LLM Background] Error: {e}")

async def post_process_with_llm(text: str, base_url: str = None) -> str:
    """
    ASR 전사 결과를 LLM으로 후처리.
    
    OpenAI SDK를 통해 LiteLLM 프록시로 요청합니다.
    문법 교정, 구두점 추가, 띄어쓰기 교정 등을 수행합니다.
    
    Args:
        text: 원본 ASR 전사 텍스트
        base_url: (deprecated, 무시됨) LiteLLM 프록시를 사용
    
    Returns:
        후처리된 텍스트 (실패 시 원본 텍스트 반환)
    """
    try:
        client = get_async_openai_client()
        model = os.getenv("LITELLM_MODEL", LLMTier.SIMPLE)
        
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 음성 인식 결과를 교정하는 속기 전문가입니다.\n\n 어투는 바꾸지 마세요. 오직, 주어진 텍스트의 문법 교정, 적절한 구두점 추가, 잘못 전사된 것으로 보이는 키워드만 일부 교정합니다.\n\n한글과 영어 외의 전사 언어는 '음성 인식 불가'로 교정합니다. 그 외의 표현은 절대 변경하지 마세요.\n\n 반드시 다음 JSON 형식으로만 응답하세요:\n{\"original_text\": \"교정할 텍스트\", \"corrected_text\": \"교정된 텍스트\", \"reason\": \"교정한 이유(핵심 10자 이내)\"}\n\n다른 설명이나 주석을 추가하지 말고, 오직 JSON만 출력하세요."
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            temperature=0.1,
            max_tokens=500
        )
        
        content = response.choices[0].message.content
        if not content:
            logger.warning("[LLM Post-process] Empty response, using original text")
            return text
        
        content = content.strip()
        
        # JSON 파싱 시도
        try:
            # JSON 블록 추출 (```json ... ``` 형식 처리)
            if "```json" in content:
                json_start = content.find("```json") + 7
                json_end = content.find("```", json_start)
                json_content = content[json_start:json_end].strip()
            elif "```" in content:
                json_start = content.find("```") + 3
                json_end = content.find("```", json_start)
                json_content = content[json_start:json_end].strip()
            else:
                json_content = content
            
            parsed = json.loads(json_content)
            processed_text = parsed.get("corrected_text", "").strip()
            reason = parsed.get("reason", "").strip()
            
            if processed_text:
                logger.info(f"[LLM Post-process] Success (JSON): '{text}' -> '{processed_text}' (Reason: {reason})")
                return processed_text
                
        except json.JSONDecodeError:
            # JSON 파싱 실패 시, 정규식으로 텍스트 추출 시도
            match = re.search(r'"corrected_text"\s*:\s*"([^"]*)"', content)
            if match:
                processed_text = match.group(1).strip()
                if processed_text:
                    logger.info(f"[LLM Post-process] Success (regex): '{text}' -> '{processed_text}'")
                    return processed_text
            
            # 그래도 실패하면, 첫 번째 문장만 추출
            lines = content.split('\n')
            first_line = lines[0].strip()
            first_line = re.sub(r'\*\*.*?\*\*', '', first_line)
            first_line = re.sub(r'^\d+\.\s*', '', first_line)
            first_line = first_line.strip()
            
            if first_line and len(first_line) > 5:
                logger.info(f"[LLM Post-process] Success (fallback): '{text}' -> '{first_line}'")
                return first_line
            
            logger.warning("[LLM Post-process] All parsing failed, using original text")
            return text
        
        return text
            
    except Exception as e:
        logger.error(f"[LLM Post-process] Error: {e}, using original text")
        return text

