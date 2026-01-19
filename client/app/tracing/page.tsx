'use client';

import { useEffect, useState } from 'react';

export default function TracingPage() {
    const [jaegerUrl, setJaegerUrl] = useState<string>('');

    useEffect(() => {
        const envUrl = process.env.NEXT_PUBLIC_JAEGER_URL;

        if (envUrl) {
            if (envUrl.startsWith('/')) {
                // 상대 경로 그대로 사용 (Flower와 동일한 방식)
                setJaegerUrl(envUrl);
            } else if (envUrl.startsWith('http')) {
                // 절대 URL인 경우 현재 프로토콜로 변환
                const url = new URL(envUrl);
                url.protocol = window.location.protocol;
                setJaegerUrl(url.toString());
            } else {
                setJaegerUrl(`/${envUrl}`);
            }
        } else {
            // 기본값: 상대 경로 사용 (Flower와 동일한 방식, Mixed Content 방지)
            setJaegerUrl('/jaeger/');
        }
    }, []);

    if (!jaegerUrl) {
        return (
            <div className="flex flex-col h-screen">
                <div className="p-6 border-b">
                    <h1 className="text-2xl font-bold">분산 추적</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        서비스 간 요청 추적 및 병목 분석
                    </p>
                </div>
                <div className="flex-1 flex items-center justify-center">
                    <div className="text-center">
                        <h2 className="text-xl font-semibold text-muted-foreground mb-2">
                            로딩 중...
                        </h2>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="flex flex-col h-screen">
            <div className="p-6 border-b">
                <h1 className="text-2xl font-bold">분산 추적</h1>
                <p className="text-sm text-muted-foreground mt-1">
                    서비스 간 요청 추적 및 병목 분석 (Jaeger)
                </p>
            </div>
            <div className="flex-1 relative">
                <iframe
                    src={jaegerUrl}
                    className="absolute inset-0 w-full h-full border-0"
                    title="Jaeger Tracing"
                    allowFullScreen
                />
            </div>
        </div>
    )
}
