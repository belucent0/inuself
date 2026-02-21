/**
 * MonitoringPage - 모니터링 대시보드 페이지 (/monitoring)
 */

import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Tabs, TabsList, TabsTrigger } from '@/shared/components/ui/tabs';
import { ScrollText, GitBranch, Activity, ListTodo, BarChart3, Bot, Brain, ExternalLink } from 'lucide-react';
import { cn } from '@/shared/utils/cn';
import { LangfuseOverviewPanel } from '@/features/monitoring/components/LangfuseOverviewPanel';

interface DashboardTab {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  path: string;
  description: string;
  external?: boolean;
}

const DASHBOARD_TABS: DashboardTab[] = [
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
  {
    id: 'pipeline',
    label: '작업 현황',
    icon: BarChart3,
    path: '/grafana/d/content-pipeline/content-pipeline?kiosk=true&orgId=1&refresh=30s',
    description: '콘텐츠 처리 파이프라인 (ASR/OCR/요약)',
  },
  {
    id: 'llm',
    label: 'LLM 메트릭',
    icon: Bot,
    path: '/grafana/d/llm-observability/llm-observability?kiosk=true&orgId=1&refresh=30s',
    description: 'LLM 실시간 메트릭 (OpenLLMetry)',
  },
  {
    id: 'langfuse-overview',
    label: 'LLM 품질',
    icon: Brain,
    path: '',
    description: 'Langfuse Trace/Score 기반 내부 품질 대시보드',
  },
  {
    id: 'langfuse',
    label: 'LLM 분석',
    icon: Brain,
    path: '/langfuse/',
    description: 'LLM Observability & Prompt Management (Langfuse)',
  },
];

export function MonitoringPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const activeTab =
    tabParam && DASHBOARD_TABS.some((tab) => tab.id === tabParam)
      ? tabParam
      : 'logs';

  const monitoringBaseUrl = useMemo(() => {
    if (typeof window === 'undefined') {
      return '';
    }

    if (window.location.port === '3000') {
      return `${window.location.protocol}//${window.location.hostname}`;
    }

    return '';
  }, []);

  const handleTabChange = (value: string) => {
    setSearchParams({ tab: value });
  };

  const resolveTabPath = (path: string) => {
    if (!path.startsWith('/') || monitoringBaseUrl.length === 0) {
      return path;
    }
    return `${monitoringBaseUrl}${path}`;
  };

  const currentTab = DASHBOARD_TABS.find((tab) => tab.id === activeTab) || DASHBOARD_TABS[0];
  const currentTabPath = resolveTabPath(currentTab.path);

  return (
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="px-6 py-3">
          {currentTab.external && (
            <div className="flex justify-end mb-2">
              <a
                href={currentTabPath}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-primary hover:text-primary/80 transition-colors"
              >
                새 창에서 열기
                <ExternalLink className="h-4 w-4" />
              </a>
            </div>
          )}

          {/* 탭 네비게이션 */}
          <Tabs value={activeTab} onValueChange={handleTabChange}>
            <TabsList className="h-auto p-1 bg-muted/50">
              {DASHBOARD_TABS.map((tab) => {
                const Icon = tab.icon;
                return (
                  <TabsTrigger
                    key={tab.id}
                    value={tab.id}
                    className={cn(
                      'flex items-center gap-2 px-4 py-2.5 data-[state=active]:bg-background',
                      'transition-all hover:bg-background/50'
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    <span>{tab.label}</span>
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </Tabs>

          {/* 현재 탭 설명 */}
          <div className="mt-3 flex items-start gap-2 text-sm text-muted-foreground">
            <currentTab.icon className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <p>{currentTab.description}</p>
          </div>
        </div>
      </div>

      {/* 대시보드 컨텐츠 */}
      <div className="flex-1 relative bg-muted/30">
        {currentTab.id === 'langfuse-overview' ? (
          <LangfuseOverviewPanel />
        ) : currentTab.external ? (
          // 외부 링크는 iframe 대신 안내 메시지
          <div className="flex items-center justify-center h-full">
            <div className="text-center space-y-4 p-8">
              <currentTab.icon className="h-16 w-16 mx-auto text-muted-foreground/50" />
              <div>
                <h3 className="text-lg font-semibold mb-2">{currentTab.label}</h3>
                <p className="text-sm text-muted-foreground mb-4">{currentTab.description}</p>
                <a
                  href={currentTabPath}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                >
                  새 창에서 열기
                  <ExternalLink className="h-4 w-4" />
                </a>
              </div>
            </div>
          </div>
        ) : (
          // iframe으로 대시보드 임베드
            <iframe
              key={currentTab.id}
              src={currentTabPath}
              className="w-full h-full border-0"
              title={`${currentTab.label} Dashboard`}
              sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads"
            />
        )}
      </div>
    </div>
  );
}
