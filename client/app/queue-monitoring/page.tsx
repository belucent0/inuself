'use client';

import { useEffect, useState } from 'react';

export default function QueueMonitoringPage() {
    const [flowerUrl, setFlowerUrl] = useState<string>('');

    useEffect(() => {
        const envUrl = process.env.NEXT_PUBLIC_FLOWER_URL;

        if (envUrl) {
            // 환경변수가 상대 경로면 그대로, 절대 URL이면 프로토콜 맞추기
            if (envUrl.startsWith('/')) {
                setFlowerUrl(envUrl);
            } else if (envUrl.startsWith('http')) {
                // HTTPS 사이트에서 HTTP URL 사용 시 프로토콜 자동 변환
                const url = new URL(envUrl);
                url.protocol = window.location.protocol;
                setFlowerUrl(url.toString());
            } else {
                setFlowerUrl(`/${envUrl}`);
            }
        } else {
            // 기본값: 상대 경로 사용 (Mixed Content 방지)
            setFlowerUrl('/flower/');
        }
    }, []);

    if (!flowerUrl) {
        return (
            <div className="flex flex-col h-screen">
                <div className="p-6 border-b">
                    <h1 className="text-2xl font-bold">큐 모니터링</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Celery 작업 큐 및 워커 상태 모니터링
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
                <h1 className="text-2xl font-bold">큐 모니터링</h1>
                <p className="text-sm text-muted-foreground mt-1">
                    Celery 작업 큐 및 워커 상태 모니터링 (Flower)
                </p>
            </div>
            <div className="flex-1 relative">
                <iframe
                    src={flowerUrl}
                    className="absolute inset-0 w-full h-full border-0"
                    title="Flower Dashboard"
                    allowFullScreen
                />
            </div>
        </div>
    )
}
