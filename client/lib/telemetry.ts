'use client';

import { Span } from '@opentelemetry/api';

// OpenTelemetry 브라우저 추적
// 패키지가 없거나 초기화 실패 시 graceful fallback

let initialized = false;
let telemetryEnabled = false;

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
        const { FetchInstrumentation } = await import('@opentelemetry/instrumentation-fetch');
        const { registerInstrumentations } = await import('@opentelemetry/instrumentation');

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

        // fetch 요청 자동 계측
        registerInstrumentations({
            instrumentations: [
                new FetchInstrumentation({
                    // API 요청만 추적 (외부 리소스 제외)
                    ignoreUrls: [
                        /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf)$/,
                        // Next.js 내부 요청 제외
                        /\/next\//,
                        /\/_next\//,
                    ],
                    // traceparent 헤더 전파 - 모든 API 요청에 적용
                    propagateTraceHeaderCorsUrls: [
                        /^https?:\/\/localhost/,
                        /^https?:\/\/127\.0\.0\.1/,
                        /^https?:\/\/asr\.timblo\.io/,
                        /\/api\//,  // 상대 경로 API 요청도 포함
                    ],
                }),
            ],
        });

        telemetryEnabled = true;
        console.log('[Telemetry] Browser OpenTelemetry initialized');
    } catch (e) {
        // 패키지가 없거나 초기화 실패 - 무시
        throw e;
    }
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
