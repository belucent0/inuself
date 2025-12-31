/**
 * 재사용 가능한 WebSocket Hook
 * 
 * 자동 재연결, 타입 안전성, 이벤트 핸들러를 제공하는 범용 WebSocket Hook
 */
import { useEffect, useRef, useState, useCallback } from 'react'

export interface UseWebSocketOptions<T = any> {
    /** 메시지 수신 시 호출될 콜백 */
    onMessage?: (data: T) => void
    /** 연결 성공 시 호출될 콜백 */
    onOpen?: () => void
    /** 연결 종료 시 호출될 콜백 */
    onClose?: () => void
    /** 에러 발생 시 호출될 콜백 */
    onError?: (error: Event) => void
    /** 자동 재연결 활성화 (기본: true) */
    reconnect?: boolean
    /** 재연결 시도 간격 (밀리초, 기본: 3000) */
    reconnectInterval?: number
    /** 최대 재연결 시도 횟수 (기본: 5, 0 = 무제한) */
    maxReconnectAttempts?: number
}

export interface UseWebSocketReturn<T = any> {
    /** WebSocket 연결 상태 */
    isConnected: boolean
    /** 마지막으로 수신한 메시지 */
    lastMessage: T | null
    /** 메시지 전송 함수 */
    sendMessage: (data: any) => void
    /** 수동 재연결 함수 */
    reconnect: () => void
    /** 연결 종료 함수 */
    disconnect: () => void
}

/**
 * WebSocket Hook
 * 
 * @param url - WebSocket URL (null이면 연결하지 않음)
 * @param options - Hook 옵션
 * @returns WebSocket 제어 객체
 */
export function useWebSocket<T = any>(
    url: string | null,
    options: UseWebSocketOptions<T> = {}
): UseWebSocketReturn<T> {
    // 옵션을 ref에 저장하여 connect 함수 의존성 제거
    // 이를 통해 options 객체가 매번 새로 생성되더라도 connect 함수가 재생성되는 것을 방지
    const optionsRef = useRef(options)
    useEffect(() => {
        optionsRef.current = options
    }, [options])

    const [isConnected, setIsConnected] = useState(false)
    const [lastMessage, setLastMessage] = useState<T | null>(null)

    const wsRef = useRef<WebSocket | null>(null)
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
    const reconnectAttemptsRef = useRef(0)
    const shouldConnectRef = useRef(true)

    // WebSocket URL을 http → ws로 변환
    const getWebSocketUrl = useCallback((wsUrl: string): string => {
        if (wsUrl.startsWith('ws://') || wsUrl.startsWith('wss://')) {
            return wsUrl
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const host = window.location.host

        if (wsUrl.startsWith('/')) {
            return `${protocol}//${host}${wsUrl}`
        }

        return `${protocol}//${host}/${wsUrl}`
    }, [])

    // 연결 함수
    const connect = useCallback(() => {
        if (!url || !shouldConnectRef.current) return

        // 중복 연결 방지: 이미 연결되어 있거나 연결 중이면 무시
        if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
            return
        }

        try {
            const wsUrl = getWebSocketUrl(url)
            console.log('[useWebSocket] Connecting to:', wsUrl)

            const ws = new WebSocket(wsUrl)
            wsRef.current = ws

            ws.onopen = () => {
                // Strict Mode 등으로 인해 언마운트된 경우 즉시 종료
                if (!shouldConnectRef.current) {
                    console.log('[useWebSocket] Connection opened but should close (unmounted)')
                    ws.close()
                    return
                }

                console.log('[useWebSocket] Connected')
                setIsConnected(true)
                reconnectAttemptsRef.current = 0
                optionsRef.current.onOpen?.()
            }

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data) as T
                    setLastMessage(data)
                    optionsRef.current.onMessage?.(data)
                } catch (error) {
                    console.error('[useWebSocket] Failed to parse message:', error)
                }
            }

            ws.onclose = () => {
                console.log('[useWebSocket] Disconnected')
                setIsConnected(false)
                wsRef.current = null
                optionsRef.current.onClose?.()

                // 자동 재연결
                if (shouldConnectRef.current && optionsRef.current.reconnect !== false) {
                    const maxAttempts = optionsRef.current.maxReconnectAttempts ?? 5
                    const interval = optionsRef.current.reconnectInterval ?? 3000

                    if (maxAttempts === 0 || reconnectAttemptsRef.current < maxAttempts) {
                        reconnectAttemptsRef.current++
                        // 지수 백오프 적용: 기본 간격 * (2 ^ 시도횟수), 최대 30초
                        const backoffDelay = Math.min(
                            interval * Math.pow(2, reconnectAttemptsRef.current - 1),
                            30000
                        )

                        console.log(
                            `[useWebSocket] Reconnecting... (attempt ${reconnectAttemptsRef.current}, delay: ${backoffDelay}ms)`
                        )
                        reconnectTimeoutRef.current = setTimeout(() => {
                            connect()
                        }, backoffDelay)
                    } else {
                        console.log('[useWebSocket] Max reconnect attempts reached')
                    }
                }
            }

            ws.onerror = (error) => {
                // [Noise Filter] 연결 종료 시점의 에러는 개발 모드 노이즈일 가능성이 높으므로 무시
                if (ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
                    return
                }
                console.error('[useWebSocket] Error:', error)
                optionsRef.current.onError?.(error)
            }
        } catch (error) {
            console.error('[useWebSocket] Connection failed:', error)
        }
    }, [url, getWebSocketUrl]) // 의존성을 url과 getWebSocketUrl로 최소화

    // 메시지 전송
    const sendMessage = useCallback((data: any) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(data))
        } else {
            console.warn('[useWebSocket] WebSocket is not connected')
        }
    }, [])

    // 수동 재연결
    const manualReconnect = useCallback(() => {
        disconnect()
        reconnectAttemptsRef.current = 0
        shouldConnectRef.current = true
        connect()
    }, [connect]) // disconnect는 아래 정의되나 호이스팅 문제 없음 (그러나 dependency cycle 주의 -> 여기서는 connect만 의존)

    // 연결 종료
    const disconnect = useCallback(() => {
        shouldConnectRef.current = false

        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current)
            reconnectTimeoutRef.current = null
        }

        if (wsRef.current) {
            // onclose 핸들러를 제거하여 의도적인 close 시 재연결 로직이 실행되지 않도록 함
            wsRef.current.onclose = null
            wsRef.current.close()
            wsRef.current = null
        }

        setIsConnected(false)
    }, [])

    // URL 변경 또는 마운트 시 연결
    useEffect(() => {
        shouldConnectRef.current = true

        if (url) {
            connect()
        }

        return () => {
            shouldConnectRef.current = false
            disconnect()
        }
    }, [url, connect, disconnect])

    return {
        isConnected,
        lastMessage,
        sendMessage,
        reconnect: manualReconnect,
        disconnect,
    }
}
