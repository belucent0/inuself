'use client';

import { useEffect, useState } from 'react';

export default function MonitoringPage() {
    const [dashboardUrl, setDashboardUrl] = useState<string>('');

    useEffect(() => {
        // 환경변수에서 Grafana 대시보드 URL 가져오기
        const envUrl = process.env.NEXT_PUBLIC_GRAFANA_DASHBOARD_URL;
        
        if (envUrl) {
            // 환경변수가 절대 URL인 경우 그대로 사용, 상대 경로인 경우 base URL 추가
            const fullUrl = envUrl.startsWith('http') 
                ? envUrl 
                : `${window.location.origin}${envUrl.startsWith('/') ? envUrl : '/' + envUrl}`;
            setDashboardUrl(fullUrl);
        } else {
            // 기본값: 하드코딩된 경로 사용
            const baseUrl = window.location.origin;
            const grafanaPath = '/grafana/d/windows-metrics/windows-system-metrics';
            const params = '?kiosk=true&orgId=1&from=now-15m&to=now&timezone=browser&refresh=5s';
            setDashboardUrl(`${baseUrl}${grafanaPath}${params}`);
        }
    }, []);

    if (!dashboardUrl) {
        return (
            <div className="flex flex-col h-screen">
                <div className="p-6 border-b">
                    <h1 className="text-2xl font-bold">시스템 모니터링</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        실시간 시스템 리소스 및 애플리케이션 메트릭
                    </p>
                </div>
                <div className="flex-1 flex items-center justify-center">
                    <div className="text-center">
                        <h2 className="text-xl font-semibold text-muted-foreground mb-2">
                            현재 지원하지 않는 페이지입니다
                        </h2>
                        <p className="text-sm text-muted-foreground">
                            관리자에게 문의하여 Grafana 대시보드를 설정해주세요.
                        </p>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="flex flex-col h-screen">
            <div className="p-6 border-b">
                <h1 className="text-2xl font-bold">시스템 모니터링</h1>
                <p className="text-sm text-muted-foreground mt-1">
                    실시간 시스템 리소스 및 애플리케이션 메트릭
                </p>
            </div>
            <div className="flex-1 relative">
                <iframe
                    src={dashboardUrl}
                    className="absolute inset-0 w-full h-full border-0"
                    title="Grafana Dashboard"
                    allowFullScreen
                />
            </div>
        </div>
    )
}
