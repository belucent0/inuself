'use client';

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollText, GitBranch, Activity, ListTodo } from 'lucide-react';

// 상대 경로 사용 - localhost, asr.timblo.io 모두 동작
const DASHBOARD_TABS = [
    {
        id: 'logs',
        label: '로그',
        icon: ScrollText,
        path: '/grafana/d/docker-logs/docker-logs?kiosk=true&orgId=1&from=now-15m&to=now&refresh=5s',
        description: '중앙 집중식 로그 조회 (Loki)',
    },
    {
        id: 'traces',
        label: '트레이스',
        icon: GitBranch,
        path: '/grafana/explore?schemaVersion=1&panes=%7B%22v4e%22%3A%7B%22datasource%22%3A%22tempo%22%2C%22queries%22%3A%5B%7B%22refId%22%3A%22A%22%2C%22queryType%22%3A%22traceqlSearch%22%7D%5D%7D%7D&orgId=1&kiosk=true',
        description: '분산 추적 (Tempo)',
    },
    {
        id: 'metrics',
        label: '메트릭',
        icon: Activity,
        path: '/grafana/d/windows-metrics/windows-system-metrics?kiosk=true&orgId=1&from=now-15m&to=now&refresh=5s',
        description: '시스템 메트릭 (Grafana)',
    },
    {
        id: 'queue',
        label: '작업 큐',
        icon: ListTodo,
        path: '/flower/',
        description: 'Celery 작업 큐 및 워커 상태 (Flower)',
    },
];

function MonitoringContent() {
    const searchParams = useSearchParams();
    const tabFromUrl = searchParams.get('tab');

    // URL 파라미터가 유효한 탭 ID인지 확인
    const validTabIds = DASHBOARD_TABS.map(t => t.id);
    const initialTab = tabFromUrl && validTabIds.includes(tabFromUrl) ? tabFromUrl : 'logs';

    const [activeTab, setActiveTab] = useState(initialTab);

    // URL 파라미터 변경 시 탭 업데이트
    useEffect(() => {
        if (tabFromUrl && validTabIds.includes(tabFromUrl)) {
            setActiveTab(tabFromUrl);
        }
    }, [tabFromUrl]);

    const activeConfig = DASHBOARD_TABS.find(t => t.id === activeTab);
    const activeUrl = activeConfig?.path || '';
    const activeDescription = activeConfig?.description || '';

    return (
        <div className="flex flex-col h-screen">
            <div className="p-4 border-b flex items-center gap-6">
                <Tabs value={activeTab} onValueChange={setActiveTab}>
                    <TabsList>
                        {DASHBOARD_TABS.map(tab => {
                            const Icon = tab.icon;
                            return (
                                <TabsTrigger key={tab.id} value={tab.id} className="gap-2">
                                    <Icon className="h-4 w-4" />
                                    {tab.label}
                                </TabsTrigger>
                            );
                        })}
                    </TabsList>
                </Tabs>
                <p className="text-sm text-muted-foreground">
                    {activeDescription}
                </p>
            </div>
            <div className="flex-1 relative">
                <iframe
                    src={activeUrl}
                    className="absolute inset-0 w-full h-full border-0"
                    title={`Dashboard - ${activeTab}`}
                    allowFullScreen
                />
            </div>
        </div>
    );
}

function LoadingFallback() {
    return (
        <div className="flex flex-col h-screen">
            <div className="p-4 border-b flex items-center gap-6">
                <div className="h-10 w-64 bg-muted rounded animate-pulse" />
            </div>
            <div className="flex-1 flex items-center justify-center">
                <div className="text-muted-foreground">로딩 중...</div>
            </div>
        </div>
    );
}

export default function MonitoringPage() {
    return (
        <Suspense fallback={<LoadingFallback />}>
            <MonitoringContent />
        </Suspense>
    );
}
