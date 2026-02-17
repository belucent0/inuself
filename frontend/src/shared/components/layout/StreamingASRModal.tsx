/**
 * Streaming ASR (Automatic Speech Recognition) Modal
 * Real-time speech-to-text transcription with WebSocket connection
 */

import { useState, useRef, useEffect } from 'react'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/shared/components/ui/dialog'
import { ScrollArea } from '@/shared/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Mic, MicOff } from 'lucide-react'

interface StreamingASRModalProps {
    open: boolean
    onOpenChange: (open: boolean) => void
}

const toWebSocketBaseUrl = (httpUrl: string): string => {
    const normalized = httpUrl.replace(/\/$/, '')
    try {
        const parsed = new URL(normalized)
        parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:'
        return `${parsed.protocol}//${parsed.host}`
    } catch {
        return normalized
    }
}

const getAsrWebSocketBaseUrl = (): string => {
    const apiBaseFromEnv = import.meta.env.VITE_API_BASE_URL as string | undefined
    if (apiBaseFromEnv && apiBaseFromEnv.startsWith('http')) {
        return toWebSocketBaseUrl(apiBaseFromEnv)
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${wsProtocol}//${window.location.host}`
}

export function StreamingASRModal({ open, onOpenChange }: StreamingASRModalProps) {
    const [isRecording, setIsRecording] = useState(false)
    const [committedLines, setCommittedLines] = useState<Array<{ id: number; text: string; processedText?: string; segmentId?: string }>>([])
    const [status, setStatus] = useState<string>('서버 연결 대기 중...')
    const lineIdRef = useRef(0)
    const [isServerReady, setIsServerReady] = useState(false)

    const websocketRef = useRef<WebSocket | null>(null)
    const mediaRecorderRef = useRef<MediaRecorder | null>(null)
    const streamRef = useRef<MediaStream | null>(null)
    const isRecordingRef = useRef(false)
    const scrollAreaRef = useRef<HTMLDivElement>(null)
    const canvasRef = useRef<HTMLCanvasElement>(null)
    const animationFrameRef = useRef<number | null>(null)
    const audioContextRef = useRef<AudioContext | null>(null)
    const pendingTimeoutsRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set())
    const analyserRef = useRef<AnalyserNode | null>(null)
    const currentMaxAmplitudeRef = useRef(0)
    const hasSentHeaderRef = useRef(false)

    const [audioInputs, setAudioInputs] = useState<MediaDeviceInfo[]>([])
    const [selectedDeviceId, setSelectedDeviceId] = useState<string>('')
    const [selectedLanguage, setSelectedLanguage] = useState<string>('auto')

    useEffect(() => {
        const getDevices = async () => {
            try {
                await navigator.mediaDevices.getUserMedia({ audio: true })
                const devices = await navigator.mediaDevices.enumerateDevices()
                const inputs = devices.filter(device => device.kind === 'audioinput')
                setAudioInputs(inputs)

                if (inputs.length > 0) {
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
            navigator.mediaDevices.addEventListener('devicechange', getDevices)
        }

        return () => {
            navigator.mediaDevices.removeEventListener('devicechange', getDevices)
        }
    }, [open, selectedDeviceId])

    useEffect(() => {
        let ws: WebSocket | null = null;

        if (open) {
            const newSessionId = Math.random().toString(36).substring(2, 15);
            setCommittedLines([]);
            setStatus('서버 연결 및 모델 로딩 중...');
            setIsServerReady(false);

            const connectWebSocket = () => {
                const wsBaseUrl = getAsrWebSocketBaseUrl();
                const wsUrl = `${wsBaseUrl}/ws/asr-stream/${newSessionId}?lang=${selectedLanguage}`;

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

                                setCommittedLines((prev) => [
                                    ...prev,
                                    { id: lineId, text: textToCommit, processedText: undefined, segmentId: segmentId }
                                ]);
                            }
                        } else if (message.type === 'correction') {
                            const segmentId = message.segment_id;
                            const processedText = message.processed_text;

                            if (segmentId && processedText) {
                                const timeoutId = setTimeout(() => {
                                    setCommittedLines((prev) =>
                                        prev.map(line =>
                                            line.segmentId === segmentId
                                                ? { ...line, processedText }
                                                : line
                                        )
                                    );
                                    pendingTimeoutsRef.current.delete(timeoutId);
                                }, 100);

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
                            setTimeout(() => {
                                if (isRecordingRef.current) {
                                    setStatus("녹음 중...");
                                } else {
                                    setStatus('준비 완료');
                                }
                            }, 2000);
                        }

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
                };
            };

            connectWebSocket();

        } else {
            stopRecording();
            if (websocketRef.current) {
                websocketRef.current.close();
                websocketRef.current = null;
            }
            pendingTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
            pendingTimeoutsRef.current.clear();
        }

        return () => {
            if (ws) {
                ws.close();
            }
            pendingTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
            pendingTimeoutsRef.current.clear();
        };
    }, [open, selectedLanguage]);


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
            if (!isRecordingRef.current) return

            animationFrameRef.current = requestAnimationFrame(draw)

            analyserRef.current!.getByteTimeDomainData(dataArray)

            let maxAmp = 0;
            for (let i = 0; i < bufferLength; i++) {
                const amp = Math.abs(dataArray[i] - 128);
                if (amp > maxAmp) maxAmp = amp;
            }
            if (maxAmp > currentMaxAmplitudeRef.current) {
                currentMaxAmplitudeRef.current = maxAmp;
            }

            canvasCtx.fillStyle = 'rgb(255, 255, 255)'
            canvasCtx.clearRect(0, 0, canvas.width, canvas.height)

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
        if (!isServerReady) return;
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

            const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext
            const audioContext = new AudioContextClass()
            audioContextRef.current = audioContext

            if (audioContext.state === 'suspended') {
                await audioContext.resume()
            }

            const analyser = audioContext.createAnalyser()
            analyser.fftSize = 2048
            analyserRef.current = analyser

            const source = audioContext.createMediaStreamSource(stream)
            source.connect(analyser)

            isRecordingRef.current = true
            currentMaxAmplitudeRef.current = 0;
            hasSentHeaderRef.current = false;
            drawWaveform()
            setIsRecording(true)
            setStatus("녹음 중...")

            let chunkCount = 0;
            let chunkIntervalId: ReturnType<typeof setInterval> | null = null;

            const createNewRecorder = () => {
                const newMimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                    ? 'audio/webm;codecs=opus' : 'audio/webm';
                const newRecorder = new MediaRecorder(stream, { mimeType: newMimeType });

                newRecorder.ondataavailable = async (event) => {
                    const ws = websocketRef.current;

                    if (event.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
                        const maxAmp = currentMaxAmplitudeRef.current;
                        currentMaxAmplitudeRef.current = 0;

                        const SILENCE_THRESHOLD = 3;
                        const MIN_VOLUME_THRESHOLD = 10;

                        if (maxAmp < SILENCE_THRESHOLD) {
                            console.log('Skipping silent chunk');
                            return;
                        } else if (maxAmp < MIN_VOLUME_THRESHOLD) {
                            setStatus(`⚠️ 목소리가 너무 작아 인식이 어렵습니다.`);
                            setTimeout(() => {
                                if (isRecordingRef.current) setStatus("녹음 중...");
                            }, 2000);
                            return;
                        }

                        chunkCount++;

                        ws.send(event.data);

                        const wsMessage = JSON.stringify({
                            type: "audio_chunk",
                            chunk_id: chunkCount,
                            is_last: false,
                        });
                        ws.send(wsMessage);

                        console.log(`Sent audio chunk ${chunkCount}, size: ${event.data.size} bytes`);

                        setStatus(`전사 중... (청크 ${chunkCount})`);
                    }
                };

                return newRecorder;
            };

            let mediaRecorder = createNewRecorder();
            mediaRecorderRef.current = mediaRecorder;
            mediaRecorder.start();

            chunkIntervalId = setInterval(() => {
                if (!isRecordingRef.current) {
                    if (chunkIntervalId) clearInterval(chunkIntervalId);
                    return;
                }

                if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
                    mediaRecorderRef.current.stop();
                }

                const newRecorder = createNewRecorder();
                mediaRecorderRef.current = newRecorder;
                newRecorder.start();
            }, 5000);

            (mediaRecorderRef.current as any)._chunkIntervalId = chunkIntervalId;

        } catch (error) {
            console.error('Error starting recording:', error)
            setStatus(`마이크 접근 오류: ${error}`)
        }
    }

    const stopRecording = () => {
        setIsRecording(false)
        isRecordingRef.current = false
        if (isServerReady) {
            setStatus('준비 완료')
        } else {
            setStatus('종료됨')
        }

        if (mediaRecorderRef.current) {
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

        if (animationFrameRef.current) {
            cancelAnimationFrame(animationFrameRef.current)
        }

        if (canvasRef.current) {
            const ctx = canvasRef.current.getContext('2d')
            if (ctx) ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)
        }
    }

    const handleToggleRecording = () => {
        if (isRecording) {
            stopRecording()

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
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="flex flex-col justify-center items-center space-y-4">
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
                                                {line.processedText || line.text}
                                            </p>
                                            {line.processedText && line.processedText !== line.text && (
                                                <div className="absolute top-1 right-0.5">
                                                    <div className="sm:hidden text-green-600 bg-green-50 w-5 h-5 rounded-full flex items-center justify-center opacity-80 shadow-sm">
                                                        <span className="text-xs leading-none">✓</span>
                                                    </div>
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
