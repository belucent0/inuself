'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function MonitoringPage() {
    const router = useRouter();

    useEffect(() => {
        // /logs 페이지의 메트릭 탭으로 리다이렉트
        router.replace('/logs?tab=metrics');
    }, [router]);

    return (
        <div className="flex flex-col h-screen">
            <div className="flex-1 flex items-center justify-center">
                <div className="text-center">
                    <h2 className="text-xl font-semibold text-muted-foreground mb-2">
                        리다이렉트 중...
                    </h2>
                </div>
            </div>
        </div>
    );
}
