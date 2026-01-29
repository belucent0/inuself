'use client';

import { Span, trace, context, SpanStatusCode } from '@opentelemetry/api';

// OpenTelemetry 브라우저 추적
// 패키지가 없거나 초기화 실패 시 graceful fallback

let initialized = false;
let telemetryEnabled = false;
let originalFetch: typeof fetch | null = null;

/**
 * 브라우저 OpenTelemetry 초기화
 * - Jaeger로 traces 전송
 * - fetch 요청 자동 추적
 * - W3C Trace Context 전파
 */
export function initTelemetry(): void {
    if (typeof window === 'undefined' || initialized) {
        return;
    }
    initialized = true;

    // 동적 import로 OpenTelemetry 로드 (패키지 없으면 graceful fail)
    initTelemetryAsync().catch((e) => {
        console.warn('[Telemetry] OpenTelemetry not available:', e.message);
    });
}

async function initTelemetryAsync(): Promise<void> {
    try {
        const { WebTracerProvider, BatchSpanProcessor } = await import('@opentelemetry/sdk-trace-web');
        const { OTLPTraceExporter } = await import('@opentelemetry/exporter-trace-otlp-http');
        const { Resource } = await import('@opentelemetry/resources');
        const { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } = await import('@opentelemetry/semantic-conventions');

        // nginx /otlp/ 프록시를 통해 Jaeger OTLP HTTP 엔드포인트로 전송
        // OTLPTraceExporter는 절대 경로를 요구하므로 window.location을 사용하여 URL 생성
        const protocol = window.location.protocol;
        const host = window.location.host;
        const defaultEndpoint = `${protocol}//${host}/otlp/v1/traces`;

        const otlpEndpoint = process.env.NEXT_PUBLIC_OTLP_ENDPOINT || defaultEndpoint;

        const resource = new Resource({
            [ATTR_SERVICE_NAME]: 'asr-frontend',
            [ATTR_SERVICE_VERSION]: '1.0.0',
        });

        const exporter = new OTLPTraceExporter({
            url: otlpEndpoint,
        });

        const provider = new WebTracerProvider({
            resource,
            spanProcessors: [new BatchSpanProcessor(exporter)],
        });

        provider.register();

        // 전역 fetch 패치 (span 이름을 "METHOD /path" 형식으로 설정 + traceparent 헤더 주입)
        // FetchInstrumentation 대신 직접 패치하여 span 이름 제어
        patchGlobalFetch();

        telemetryEnabled = true;
        console.log('[Telemetry] Browser OpenTelemetry initialized (global fetch patched)');
    } catch (e) {
        // 패키지가 없거나 초기화 실패 - 무시
        throw e;
    }
}

/**
 * 전역 fetch를 패치하여 모든 API 요청에 적절한 span 이름 설정
 * - span 이름: "METHOD /path" (예: "POST /api/contents/upload")
 * - /api/ 경로 요청만 추적 (정적 리소스 제외)
 * - traceparent 헤더 자동 주입 (분산 추적)
 */
function patchGlobalFetch(): void {
    if (originalFetch) return; // 이미 패치됨

    originalFetch = window.fetch;
    const tracer = trace.getTracer('asr-frontend');

    // 추적하지 않을 URL 패턴
    const ignorePatterns = [
        /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf)$/,
        /\/next\//,
        /\/_next\//,
        /\/health/,
        /\/ready/,
        /\/metrics/,
        /\/healthz/,
        /\/otlp\//,  // OTLP exporter 요청 제외 (무한 루프 방지)
    ];

    window.fetch = async function(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
        // URL 추출
        let url: string;
        let method = 'GET';

        if (typeof input === 'string') {
            url = input;
            method = init?.method || 'GET';
        } else if (input instanceof URL) {
            url = input.toString();
            method = init?.method || 'GET';
        } else if (input instanceof Request) {
            url = input.url;
            method = input.method || init?.method || 'GET';
        } else {
            // 알 수 없는 타입 - 원본 fetch 호출
            return originalFetch!.call(window, input, init);
        }

        // 무시할 URL인지 확인
        const shouldIgnore = ignorePatterns.some(pattern => pattern.test(url));
        if (shouldIgnore) {
            return originalFetch!.call(window, input, init);
        }

        // API 요청만 추적 (/api/ 경로)
        if (!url.includes('/api/')) {
            return originalFetch!.call(window, input, init);
        }

        // span 이름 생성: "METHOD /path"
        let path: string;
        try {
            const urlObj = new URL(url, window.location.origin);
            path = urlObj.pathname;
        } catch {
            path = url;
        }
        const spanName = `${method} ${path}`;

        // span 생성 및 fetch 실행
        return tracer.startActiveSpan(spanName, async (span) => {
            try {
                span.setAttribute('http.method', method);
                span.setAttribute('http.url', url);
                span.setAttribute('http.target', path);

                // traceparent 헤더 주입 (W3C Trace Context)
                const spanContext = span.spanContext();
                const traceparent = `00-${spanContext.traceId}-${spanContext.spanId}-01`;

                // init 객체 복사 및 헤더 추가
                const newInit: RequestInit = { ...init };
                const existingHeaders = new Headers(init?.headers);
                existingHeaders.set('traceparent', traceparent);
                newInit.headers = existingHeaders;

                const response = await originalFetch!.call(window, input, newInit);

                span.setAttribute('http.status_code', response.status);
                if (!response.ok) {
                    span.setStatus({ code: SpanStatusCode.ERROR, message: `HTTP ${response.status}` });
                }

                return response;
            } catch (error) {
                span.setStatus({ code: SpanStatusCode.ERROR, message: String(error) });
                throw error;
            } finally {
                span.end();
            }
        });
    };

    console.log('[Telemetry] Global fetch patched for tracing');
}

/**
 * telemetry 활성화 여부 확인
 */
export function isTelemetryEnabled(): boolean {
    return telemetryEnabled;
}

/**
 * 사용자 액션 추적용 속성
 */
export const ContentAttributes = {
    CONTENT_ID: 'content.id',
    CONTENT_TYPE: 'content.type',
    FILE_NAME: 'content.filename',
    FILE_SIZE: 'content.file_size_bytes',
    UPLOAD_DURATION_MS: 'content.upload_duration_ms',
} as const;
