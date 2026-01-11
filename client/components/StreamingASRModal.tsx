"use client"

import { useState, useRef, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Mic, MicOff, Settings } from 'lucide-react'

interface StreamingASRModalProps {
    open: boolean
    onOpenChange: (open: boolean) => void
}

export function StreamingASRModal({ open, onOpenChange }: StreamingASRModalProps) {
    const [isRecording, setIsRecording] = useState(false)
    // 각 라인을 객체로 저장: { id, text (원본), processedText (후처리), segmentId (Backend ID) }
    const [committedLines, setCommittedLines] = useState<Array<{ id: number; text: string; processedText?: string; segmentId?: string }>>([])
    const [status, setStatus] = useState<string>('서버 연결 대기 중...')
    const lineIdRef = useRef(0) // 각 라인에 고유 ID 부여
    const [isServerReady, setIsServerReady] = useState(false) // 서버(VRAM) 준비 여부
    const [sessionId, setSessionId] = useState<string>('')

    const websocketRef = useRef<WebSocket | null>(null)
    const mediaRecorderRef = useRef<MediaRecorder | null>(null)
    const streamRef = useRef<MediaStream | null>(null)
    const isRecordingRef = useRef(false) // 시각화 루프에서 최신 상태 참조용
    const scrollAreaRef = useRef<HTMLDivElement>(null)
    const canvasRef = useRef<HTMLCanvasElement>(null)
    const animationFrameRef = useRef<number | null>(null)
    const audioContextRef = useRef<AudioContext | null>(null)
    const pendingTimeoutsRef = useRef<Set<NodeJS.Timeout>>(new Set()) // setTimeout cleanup용
    const analyserRef = useRef<AnalyserNode | null>(null)
    const currentMaxAmplitudeRef = useRef(0) // 현재 청크 구간의 최대 진폭
    const hasSentHeaderRef = useRef(false) // WebM 헤더 전송 여부

    // 장치 목록 states
    const [audioInputs, setAudioInputs] = useState<MediaDeviceInfo[]>([])
    const [selectedDeviceId, setSelectedDeviceId] = useState<string>('')
    const [selectedLanguage, setSelectedLanguage] = useState<string>('auto')

    // 장치 목록 가져오기
    useEffect(() => {
        const getDevices = async () => {
            try {
                // 권한 요청 (장치 이름 표시를 위해 필요)
                await navigator.mediaDevices.getUserMedia({ audio: true })
                const devices = await navigator.mediaDevices.enumerateDevices()
                const inputs = devices.filter(device => device.kind === 'audioinput')
                setAudioInputs(inputs)

                // 기본 장치 선택 (첫 번째 장치 또는 'default')
                if (inputs.length > 0) {
                    // 이미 선택된 것이 없으면 첫번째꺼
                    if (!selectedDeviceId) {
                        setSelectedDeviceId(inputs[0].deviceId)
                    }
                }
            } catch (err) {
                console.error("Error enumerating devices:", err)
            }
        }

        if (open) {
            getDevices()
            // 장치 변경 감지
            navigator.mediaDevices.addEventListener('devicechange', getDevices)
        }

        return () => {
            navigator.mediaDevices.removeEventListener('devicechange', getDevices)
        }
    }, [open])

    // 세션 ID 생성 및 WebSocket 연결 관리
    useEffect(() => {
        let ws: WebSocket | null = null;

        if (open) {
            const newSessionId = Math.random().toString(36).substring(2, 15);
            setSessionId(newSessionId);
            setCommittedLines([]);
            setStatus('서버 연결 및 모델 로딩 중...');
            setIsServerReady(false);

            // WebSocket 연결 로직
            const connectWebSocket = () => {
                // localhost 이슈 방지를 위해 로컬환경에서는 127.0.0.1 사용
                let wsHost = window.location.hostname;
                if (wsHost === 'localhost') {
                    wsHost = '127.0.0.1';
                }

                let wsUrl = '';
                if (window.location.port === '3000') {
                    // 로컬 개발 환경
                    wsUrl = `ws://${wsHost}:8000/ws/asr-stream/${newSessionId}?lang=${selectedLanguage}`;
                } else {
                    // 프로덕션 또는 다른 환경
                    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    wsUrl = `${wsProtocol}//${window.location.host}/ws/asr-stream/${newSessionId}?lang=${selectedLanguage}`;
                }

                console.log('Connecting to WebSocket:', wsUrl);
                ws = new WebSocket(wsUrl);
                websocketRef.current = ws;

                ws.onopen = () => {
                    console.log('WebSocket connected');
                    setStatus('모델 로딩 중...');
                };

                ws.onmessage = (event) => {
                    try {
                        const message = JSON.parse(event.data);
                        if (message.type === 'commit') {
                            const textToCommit = message.text;
                            const segmentId = message.segment_id;

                            if (textToCommit) {
                                const lineId = lineIdRef.current++;

                                // 1. 원본 텍스트로 라인 추가 (즉시 표시)
                                setCommittedLines((prev) => [
                                    ...prev,
                                    { id: lineId, text: textToCommit, processedText: undefined, segmentId: segmentId }
                                ]);
                            }
                        } else if (message.type === 'correction') {
                            const segmentId = message.segment_id;
                            const processedText = message.processed_text;

                            if (segmentId && processedText) {
                                // 2. 해당 segmentId를 가진 라인을 찾아 업데이트
                                const timeoutId = setTimeout(() => {
                                    setCommittedLines((prev) =>
                                        prev.map(line =>
                                            line.segmentId === segmentId
                                                ? { ...line, processedText }
                                                : line
                                        )
                                    );
                                    // 완료된 timeout은 Set에서 제거
                                    pendingTimeoutsRef.current.delete(timeoutId);
                                }, 100); // 부드러운 전환을 위한 소량의 지연

                                // pending timeout 추적
                                pendingTimeoutsRef.current.add(timeoutId);
                            }
                        } else if (message.type === 'connection') {
                            console.log('Connection established:', message);
                        } else if (message.type === 'ready') {
                            console.log('Server Ready:', message);
                            setStatus('준비 완료');
                            setIsServerReady(true);
                        } else if (message.type === 'error') {
                            console.error("Server error message:", message.message);
                            setStatus(`서버 오류: ${message.message}`);
                        } else if (message.type === 'warning') {
                            setStatus(`⚠️ ${message.message}`);
                            // 2초 후 상태 복귀
                            setTimeout(() => {
                                // 현재 녹음 중인지 확인하여 상태 복구
                                if (isRecordingRef.current) {
                                    setStatus("녹음 중...");
                                } else if (isServerReady) {
                                    setStatus('준비 완료');
                                }
                            }, 2000);
                        }

                        // 자동 스크롤
                        if (scrollAreaRef.current) {
                            setTimeout(() => {
                                const scrollContainer = scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]');
                                if (scrollContainer) {
                                    scrollContainer.scrollTop = scrollContainer.scrollHeight;
                                }
                            }, 100);
                        }
                    } catch (e) {
                        console.error('Failed to parse websocket message:', e);
                    }
                };

                ws.onerror = (error) => {
                    console.error('WebSocket error details:', error);
                    setStatus('연결 오류 발생 (콘솔 확인)');
                };

                ws.onclose = (event) => {
                    console.log('WebSocket closed:', event.code, event.reason);
                    if (isRecordingRef.current) {
                        stopRecording();
                    }
                    // 모달이 닫혀서 끊긴 게 아니라면 재연결 로직이 필요할 수도 있음
                };
            };

            connectWebSocket();

        } else {
            // 모달이 닫히면 정리
            stopRecording();
            if (websocketRef.current) {
                websocketRef.current.close();
                websocketRef.current = null;
            }
            // pending된 모든 timeout 정리
            pendingTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
            pendingTimeoutsRef.current.clear();
        }

        return () => {
            // cleanup (컴포넌트 언마운트 시에도 닫음)
            if (ws) {
                ws.close();
            }
            // pending된 모든 timeout 정리
            pendingTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
            pendingTimeoutsRef.current.clear();
        };
    }, [open, selectedLanguage]); // selectedLanguage 변경 시 재연결? (일단 포함)


    // 시각화 정리
    useEffect(() => {
        return () => {
            if (animationFrameRef.current) {
                cancelAnimationFrame(animationFrameRef.current)
            }
            if (audioContextRef.current) {
                audioContextRef.current.close()
            }
        }
    }, [])

    const drawWaveform = () => {
        if (!analyserRef.current || !canvasRef.current) return

        const canvas = canvasRef.current
        const canvasCtx = canvas.getContext('2d')
        if (!canvasCtx) return

        const bufferLength = analyserRef.current.fftSize
        const dataArray = new Uint8Array(bufferLength)

        const draw = () => {
            if (!isRecordingRef.current) return // Ref 사용하여 최신 상태 참조

            animationFrameRef.current = requestAnimationFrame(draw)

            analyserRef.current!.getByteTimeDomainData(dataArray)

            // 진폭 계산 및 업데이트
            let maxAmp = 0;
            for (let i = 0; i < bufferLength; i++) {
                const amp = Math.abs(dataArray[i] - 128);
                if (amp > maxAmp) maxAmp = amp;
            }
            // 현재 청크 구간의 최대 등폭 갱신 (더 큰 값이 있으면 유지)
            if (maxAmp > currentMaxAmplitudeRef.current) {
                currentMaxAmplitudeRef.current = maxAmp;
            }

            // 캔버스 크기 확인 및 조정
            if (canvas.width !== canvas.offsetWidth || canvas.height !== canvas.offsetHeight) {
                // 필요하다면 리사이징 (여기서는 고정 크기로 가정)
            }

            canvasCtx.fillStyle = 'rgb(255, 255, 255)'
            canvasCtx.clearRect(0, 0, canvas.width, canvas.height) // 전체 지우기

            canvasCtx.lineWidth = 2
            canvasCtx.strokeStyle = 'rgb(59, 130, 246)'

            canvasCtx.beginPath()

            const sliceWidth = canvas.width * 1.0 / bufferLength
            let x = 0

            for (let i = 0; i < bufferLength; i++) {
                const v = dataArray[i] / 128.0
                const y = v * canvas.height / 2

                if (i === 0) {
                    canvasCtx.moveTo(x, y)
                } else {
                    canvasCtx.lineTo(x, y)
                }

                x += sliceWidth
            }

            canvasCtx.lineTo(canvas.width, canvas.height / 2)
            canvasCtx.stroke()
        }

        draw()
    }

    const startRecording = async () => {
        if (!isServerReady) return; // 서버 준비 안됨
        if (!websocketRef.current || websocketRef.current.readyState !== WebSocket.OPEN) {
            console.error("WebSocket is not open");
            setStatus("오류: 서버 연결 끊김");
            return;
        }

        try {
            console.log('Requesting microphone access with deviceId:', selectedDeviceId)
            const constraints: MediaStreamConstraints = {
                audio: selectedDeviceId ? { deviceId: { exact: selectedDeviceId } } : true
            }

            const stream = await navigator.mediaDevices.getUserMedia(constraints)
            streamRef.current = stream

            // Audio Context 및 Visualizer 설정
            const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext
            const audioContext = new AudioContextClass()
            audioContextRef.current = audioContext

            // Safari 호환성 등 이슈 방지를 위해 resume 호출
            if (audioContext.state === 'suspended') {
                await audioContext.resume()
            }

            const analyser = audioContext.createAnalyser()
            analyser.fftSize = 2048
            analyserRef.current = analyser

            const source = audioContext.createMediaStreamSource(stream)
            source.connect(analyser)

            // 시각화 시작
            isRecordingRef.current = true
            currentMaxAmplitudeRef.current = 0; // 초기화
            hasSentHeaderRef.current = false; // 헤더 플래그 초기화
            drawWaveform()
            setIsRecording(true)
            setStatus("녹음 중...")

            // 실시간 스트리밍: 5초마다 독립적인 WebM 청크 전송
            let chunkCount = 0;
            let chunkIntervalId: NodeJS.Timeout | null = null;
            
            const createNewRecorder = () => {
                const newMimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                    ? 'audio/webm;codecs=opus' : 'audio/webm';
                const newRecorder = new MediaRecorder(stream, { mimeType: newMimeType });
                
                newRecorder.ondataavailable = async (event) => {
                    const ws = websocketRef.current;
                    
                    if (event.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
                        // VAD 필터링
                        const maxAmp = currentMaxAmplitudeRef.current;
                        currentMaxAmplitudeRef.current = 0;
                        
                        const SILENCE_THRESHOLD = 3;
                        const MIN_VOLUME_THRESHOLD = 10;
                        
                        if (maxAmp < SILENCE_THRESHOLD) {
                            console.log('Skipping silent chunk');
                            return; // 침묵은 전송 안 함
                        } else if (maxAmp < MIN_VOLUME_THRESHOLD) {
                            setStatus(`⚠️ 목소리가 너무 작아 인식이 어렵습니다.`);
                            setTimeout(() => {
                                if (isRecordingRef.current) setStatus("녹음 중...");
                            }, 2000);
                            return;
                        }
                        
                        // 오디오 청크를 전송 (독립적인 WebM 파일)
                        chunkCount++;
                        
                        // 1. 먼저 바이너리 오디오 데이터 전송
                        ws.send(event.data);
                        
                        // 2. 그 다음 JSON 제어 메시지 전송 (전사 트리거)
                        const wsMessage = JSON.stringify({
                            type: "audio_chunk",
                            chunk_id: chunkCount,
                            is_last: false,
                        });
                        ws.send(wsMessage);
                        
                        console.log(`Sent audio chunk ${chunkCount}, size: ${event.data.size} bytes`);
                        
                        // 진행 상태
                        setStatus(`전사 중... (청크 ${chunkCount})`);
                    }
                };
                
                return newRecorder;
            };
            
            // 첫 번째 레코더 생성 및 시작
            let mediaRecorder = createNewRecorder();
            mediaRecorderRef.current = mediaRecorder;
            mediaRecorder.start();
            
            // 5초마다 레코더 재시작 (각 청크가 독립적인 WebM 파일이 됨)
            chunkIntervalId = setInterval(() => {
                if (!isRecordingRef.current) {
                    if (chunkIntervalId) clearInterval(chunkIntervalId);
                    return;
                }
                
                // 현재 레코더 중지 (ondataavailable 트리거)
                if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
                    mediaRecorderRef.current.stop();
                }
                
                // 새 레코더 생성 및 시작
                const newRecorder = createNewRecorder();
                mediaRecorderRef.current = newRecorder;
                newRecorder.start();
            }, 5000);
            
            // cleanup을 위해 intervalId 저장
            (mediaRecorderRef.current as any)._chunkIntervalId = chunkIntervalId;

        } catch (error) {
            console.error('Error starting recording:', error)
            setStatus(`마이크 접근 오류: ${error}`)
        }
    }

    const stopRecording = () => {
        setIsRecording(false)
        isRecordingRef.current = false
        // 녹음만 중지, 연결은 유지
        if (isServerReady) {
            setStatus('준비 완료')
        } else {
            setStatus('종료됨')
        }

        if (mediaRecorderRef.current) {
            // interval 정리
            const intervalId = (mediaRecorderRef.current as any)._chunkIntervalId;
            if (intervalId) clearInterval(intervalId);
            
            if (mediaRecorderRef.current.state !== 'inactive') {
                mediaRecorderRef.current.stop();
            }
        }

        if (streamRef.current) {
            streamRef.current.getTracks().forEach(track => track.stop())
            streamRef.current = null
        }

        // WebSocket은 여기서 닫지 않음 (모달 닫힐 때 닫음)

        if (animationFrameRef.current) {
            cancelAnimationFrame(animationFrameRef.current)
        }

        // 캔버스 초기화
        if (canvasRef.current) {
            const ctx = canvasRef.current.getContext('2d')
            if (ctx) ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)
        }
    }

    const handleToggleRecording = () => {
        if (isRecording) {
            // 녹음 중지 후 finish 메시지 전송
            stopRecording()
            
            // finish 메시지 전송 (오디오 전사 요청)
            if (websocketRef.current && websocketRef.current.readyState === WebSocket.OPEN) {
                websocketRef.current.send(JSON.stringify({ type: "finish" }));
                console.log('Sent finish message');
            }
        } else {
            startRecording()
        }
    }

    return (
        <Dialog open={open} onOpenChange={(val) => {
            if (!val) stopRecording()
            onOpenChange(val)
        }}>
            <DialogContent className="sm:max-w-xl">
                <DialogHeader>
                    <DialogTitle>Streaming ASR</DialogTitle>
                    <DialogDescription>
                        마이크 버튼을 눌러 녹음을 시작하세요.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col space-y-2 py-2">
                    {/* 마이크 및 언어 선택 */}
                    <div className="flex justify-center w-full px-10 gap-2">
                        <Select value={selectedDeviceId} onValueChange={setSelectedDeviceId} disabled={isRecording}>
                            <SelectTrigger className="w-[60%]">
                                <SelectValue placeholder="마이크 선택" />
                            </SelectTrigger>
                            <SelectContent>
                                {audioInputs.map((device) => (
                                    <SelectItem key={device.deviceId} value={device.deviceId}>
                                        {device.label || `Microphone ${device.deviceId.slice(0, 5)}...`}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        <Select value={selectedLanguage} onValueChange={setSelectedLanguage} disabled={isRecording}>
                            <SelectTrigger className="w-[40%]">
                                <SelectValue placeholder="언어" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="auto">자동감지</SelectItem>
                                {/* <SelectItem value="ko">한국어</SelectItem>
                                <SelectItem value="en">English</SelectItem>
                                <SelectItem value="ja">日本語</SelectItem>
                                <SelectItem value="zh">中文</SelectItem> */}
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="flex flex-col justify-center items-center space-y-4">
                        {/* 캔버스 영역 */}
                        <div className="w-full h-14 bg-secondary/20 rounded-md overflow-hidden flex items-center justify-center relative">
                            <canvas
                                ref={canvasRef}
                                width={500}
                                height={96}
                                className="w-full h-full"
                            />
                            {!isRecording && (
                                <div className="absolute inset-0 flex items-center justify-center text-muted-foreground text-sm">

                                </div>
                            )}
                        </div>

                        <Button
                            variant={isRecording ? "destructive" : "outline"}
                            size="icon"
                            className={`h-16 w-16 rounded-full transition-all duration-300 hover:scale-105 shadow-lg ${!isServerReady ? "opacity-50 cursor-not-allowed" : ""}`}
                            onClick={handleToggleRecording}
                            disabled={!isServerReady}
                        >
                            {isRecording ? (
                                <MicOff className="h-10 w-10 animate-pulse" />
                            ) : (
                                <Mic className={`h-10 w-10 ${!isServerReady ? 'text-gray-400' : ''}`} />
                            )}
                        </Button>
                    </div>

                    <div className="text-center text-sm font-medium text-muted-foreground">
                        {status}
                    </div>

                    <div className="border rounded-md p-2 bg-muted/30">
                        <ScrollArea className="h-[300px] w-full" ref={scrollAreaRef}>
                            {committedLines.length > 0 ? (
                                <div className="flex flex-col gap-2">
                                    {committedLines.map((line) => (
                                        <div key={line.id} className="relative">
                                            <p className="whitespace-pre-wrap text-lg leading-relaxed text-foreground bg-white/50 p-2 rounded-md shadow-sm transition-all duration-300">
                                                {/* 후처리된 텍스트가 있으면 표시, 없으면 원본 표시 */}
                                                {line.processedText || line.text}
                                            </p>
                                            {/* 후처리 완료 표시 (선택사항) */}
                                            {line.processedText && line.processedText !== line.text && (
                                                <div className="absolute top-1 right-0.5">
                                                    {/* 모바일: 작은 체크마크만 */}
                                                    <div className="sm:hidden text-green-600 bg-green-50 w-5 h-5 rounded-full flex items-center justify-center opacity-80 shadow-sm">
                                                        <span className="text-xs leading-none">✓</span>
                                                    </div>
                                                    {/* 데스크톱: 체크마크 + 텍스트 */}
                                                    <div className="hidden sm:flex text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full opacity-70 items-center gap-0.5">
                                                        ✓ 교정됨
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                    {isRecording && (
                                        <div className="flex items-center gap-2 text-muted-foreground animate-pulse mt-2">
                                            <div className="w-2 h-2 bg-blue-500 rounded-full" />
                                            <span>듣고 있는 중...</span>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="h-full flex items-center justify-center text-muted-foreground/50">
                                    {isRecording ? (
                                        <p className="animate-pulse">듣고 있습니다...</p>
                                    ) : (
                                        <p>녹음을 시작하면 여기에 텍스트가 표시됩니다...</p>
                                    )}
                                </div>
                            )}
                        </ScrollArea>
                    </div>
                </div>

                <DialogFooter className="sm:justify-between">
                    <div className="flex items-center text-xs text-muted-foreground">
                        맥락이 충분히 유지되어야 정확한 음성 인식이 가능합니다.
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
