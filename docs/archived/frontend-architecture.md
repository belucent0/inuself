# Frontend Architecture

> Next.js 16 App Router + React 19 + Context API 기반 프론트엔드 아키텍처

## 개요

이 문서는 torch-test 프로젝트의 프론트엔드 아키텍처를 설명합니다.

## 기술 스택

| 기술 | 버전 | 용도 |
|------|------|------|
| **Next.js** | 16.1.1 | React 프레임워크 (App Router) |
| **React** | 19.2.3 | UI 라이브러리 |
| **TypeScript** | 5.3.3 | 타입 안정성 |
| **Tailwind CSS** | 3.4.1 | 스타일링 |
| **ShadcN UI** | - | UI 컴포넌트 라이브러리 |
| **Vercel AI SDK** | 6.0.23 | AI 채팅 인터페이스 패턴 |
| **React Markdown** | 9.1.0 | 마크다운 렌더링 |
| **Sonner** | 1.7.0 | 토스트 알림 |
| **OpenTelemetry** | - | 클라이언트 분산 추적 |

## 디렉토리 구조

```
client/
├── app/                      # Next.js App Router 페이지
│   ├── layout.tsx           # 루트 레이아웃 (Provider, 헤더)
│   ├── page.tsx             # 홈페이지 (랜딩)
│   └── chat/
│       └── [threadId]/
│           └── page.tsx     # 스레드 상세 페이지
├── components/              # React 컴포넌트
│   ├── ai-chat/            # AI 채팅 관련 컴포넌트
│   │   └── ChatArea.tsx    # 채팅 메시지 및 입력 영역
│   ├── ui/                 # ShadcN UI 컴포넌트
│   └── app-sidebar.tsx     # 애플리케이션 사이드바
├── lib/                     # 유틸리티 및 라이브러리
│   ├── api/                # API 클라이언트
│   │   └── threads.ts      # 스레드 API
│   ├── contexts/           # React Context
│   │   └── ThreadTitleContext.tsx
│   └── hooks/              # Custom React Hooks
│       ├── useThreads.ts   # 스레드 목록 관리
│       ├── useThread.ts    # 단일 스레드 관리
│       └── useThreadChat.ts # 채팅 상태 관리
├── e2e/                     # Playwright E2E 테스트
│   └── chat-scroll.spec.ts
└── types/                   # TypeScript 타입 정의
```

## 핵심 아키텍처 패턴

### 1. Context API 기반 상태 관리

**도입 배경**: 컴포넌트 트리가 깊어지면서 props drilling 문제 발생. 외부 라이브러리 없이 React 내장 기능만으로 상태 관리.

#### ThreadTitleContext

```typescript
// client/lib/contexts/ThreadTitleContext.tsx
interface ThreadTitleContextType {
  threadTitle: string
  setThreadTitle: (title: string) => void
  isEditingTitle: boolean
  setIsEditingTitle: (editing: boolean) => void
  onEditTitle?: () => void
  onDeleteThread?: () => void
  registerHandlers: (editHandler: () => void, deleteHandler: () => void) => void
  unregisterHandlers: () => void
}
```

**사용 흐름:**
1. `layout.tsx`에서 `ThreadTitleProvider`로 앱 전체를 감싼다
2. `page.tsx`에서 `registerHandlers`로 편집/삭제 핸들러 등록
3. `layout.tsx`의 헤더에서 `useThreadTitle` 훅으로 제목과 핸들러 소비

**장점:**
- Props drilling 제거
- 컴포넌트 간 느슨한 결합
- 외부 라이브러리 불필요

### 2. Vercel AI SDK 스타일 API 패턴

**도입 배경**: 기존 SSE 로직을 유지하면서 선언적 API 제공. 향후 AI SDK로 완전 교체 가능.

#### useThreadChat Hook

```typescript
// client/lib/hooks/useThreadChat.ts
export function useThreadChat({ threadId, initialMessages, onMessageComplete }) {
  return {
    // AI SDK 호환 API
    messages: Message[]
    input: string
    handleInputChange: (e) => void
    handleSubmit: (e) => void
    isLoading: boolean
    error: Error | null

    // 커스텀 API
    sendMessage: (content: string, mode?: string) => Promise<void>
    requestAIResponse: (query: string, mode?: string) => Promise<void>
    regenerate: (mode?: string) => Promise<void>
    isStreaming: boolean
    currentStreamingMessage: string
    currentThinkingSteps: any[]
    currentSources: any[]
  }
}
```

**특징:**
- **Optimistic Updates**: 사용자 메시지를 즉시 UI에 표시
- **SSE Streaming**: 백엔드의 Server-Sent Events 처리
- **자동 상태 관리**: 메시지 배열, 로딩 상태, 에러 처리
- **선언적 API**: `handleSubmit`, `handleInputChange` 등 React 패턴

**SSE 이벤트 타입:**
```typescript
type SSEChunk =
  | { type: 'thinking', data: any }      // 사고 과정
  | { type: 'source', data: any }        // 출처 개별 추가
  | { type: 'sources', data: any[] }     // 출처 배치 추가
  | { type: 'token', data: string }      // 토큰 단위 스트리밍
  | { type: 'content', data: string }    // 전체 내용
  | { type: 'done', data: null }         // 완료
  | { type: 'error', data: string }      // 에러
```

### 3. 레이아웃 패턴

#### Sticky Header + Fixed Input

```tsx
// client/app/layout.tsx
<header className="sticky top-0 z-40 ...">
  {/* 헤더는 스크롤 시 상단 고정 */}
</header>

// client/components/ai-chat/ChatArea.tsx
<div className="h-full flex flex-col">
  <div ref={scrollRef} className="flex-1 overflow-y-auto">
    {/* 스크롤 가능한 메시지 영역 */}
  </div>
  <div className="sticky bottom-0 z-50 ...">
    {/* 입력창은 하단 고정 */}
  </div>
</div>
```

**특징:**
- 헤더와 입력창이 항상 보임
- 사이드바 토글 시에도 레이아웃 유지
- `SidebarProvider`로 사이드바 상태 관리

### 4. OpenTelemetry 브라우저 추적

```typescript
// client/app/layout.tsx
import '@/lib/telemetry'  // 자동 초기화

// client/lib/telemetry.ts
if (typeof window !== 'undefined') {
  const provider = new WebTracerProvider({
    resource: new Resource({
      [ATTR_SERVICE_NAME]: 'torch-asr-client',
    }),
  })

  provider.addSpanProcessor(
    new BatchSpanProcessor(
      new OTLPTraceExporter({
        url: '/otlp/v1/traces',  // 백엔드 프록시
      })
    )
  )

  // Global fetch 자동 계측
  registerInstrumentations({
    instrumentations: [new FetchInstrumentation()],
  })
}
```

**특징:**
- 브라우저에서 발생하는 fetch 요청 자동 추적
- 백엔드 trace와 연결 (trace-id 전파)
- Tempo로 전송하여 End-to-End 가시성 확보

## 주요 컴포넌트

### ChatArea

채팅 메시지 표시 및 입력 영역.

**주요 기능:**
- 메시지 렌더링 (user/assistant)
- 스트리밍 중 실시간 업데이트
- 마크다운 렌더링
- 출처 표시 (SourcesModal)
- 재생성 버튼
- 자동 스크롤 (스트리밍 중)

**Props:**
```typescript
interface ChatAreaProps {
  messages: Message[]
  isStreaming: boolean
  currentStreamingMessage: string
  currentThinkingSteps?: any[]
  currentSources?: any[]
  onSendMessage: (content: string, mode?: string) => void
  onRegenerate?: () => void
}
```

### AppSidebar

애플리케이션 사이드바 (스레드 목록).

**주요 기능:**
- 스레드 목록 표시
- 새 대화 시작
- 스레드 검색
- 스레드 삭제
- 라우팅 처리

### DynamicHeader

Context API를 사용한 동적 헤더.

**주요 기능:**
- 현재 페이지 제목 표시
- 스레드 페이지에서는 스레드 제목 표시
- 편집/삭제 드롭다운 메뉴
- 사이드바 토글 버튼

## API 통신

### Thread API

```typescript
// client/lib/api/threads.ts

// 스레드 목록 조회
export async function getThreads(limit = 50, offset = 0): Promise<Thread[]>

// 단일 스레드 조회
export async function getThread(threadId: string): Promise<Thread>

// 스레드 생성
export async function createThread(title: string, firstMessage: string): Promise<Thread>

// 메시지 스트리밍 (SSE)
// POST /api/threads/{threadId}/messages/stream
// Response: text/event-stream

// 재생성
// POST /api/threads/{threadId}/regenerate
// Response: text/event-stream
```

## 성능 최적화

### 1. React 19 최적화
- **Concurrent Rendering**: 자동으로 렌더링 우선순위 관리
- **Automatic Batching**: 여러 상태 업데이트를 자동으로 배치 처리

### 2. Next.js 최적화
- **서버 컴포넌트**: 기본적으로 서버에서 렌더링 (Client 컴포넌트는 명시적으로 `"use client"`)
- **코드 스플리팅**: 동적 import로 번들 크기 최소화
- **이미지 최적화**: `next/image` 사용

### 3. 메모이제이션
```typescript
// useCallback으로 함수 메모이제이션
const handleSubmit = useCallback(async (e: React.FormEvent) => {
  // ...
}, [input, state.isLoading, append])

// useMemo는 필요시에만 사용 (과도한 사용 지양)
```

## 테스트 전략

### E2E 테스트 (Playwright)

```typescript
// client/e2e/chat-scroll.spec.ts
test('새 질문 입력 시 사용자 말풍선이 적절한 위치에 표시되어야 함', async ({ page }) => {
  await page.goto('http://localhost:3000/chat/test-thread-id')

  const textarea = page.locator('textarea[placeholder*="질문"]')
  await textarea.fill('이것은 테스트 질문입니다')
  await textarea.press('Enter')

  const userMessage = page.locator('text=이것은 테스트 질문입니다').first()
  await expect(userMessage).toBeVisible()
  await expect(userMessage).toBeInViewport()
})
```

**테스트 커버리지:**
- 사용자 상호작용 플로우
- 스크롤 동작
- 메시지 표시
- 입력창 고정 위치

## 향후 개선 방향

### 1. AI SDK 완전 통합
현재는 AI SDK 스타일 API만 차용. 백엔드가 StreamData 프로토콜을 지원하면:
```typescript
// Before (Custom)
const { messages, sendMessage } = useThreadChat({ threadId })

// After (AI SDK)
import { useChat } from '@ai-sdk/react'
const { messages, input, handleSubmit } = useChat({ api: `/api/threads/${threadId}` })
```

### 2. 상태 관리 라이브러리 도입 고려
Context API로 충분하지만, 복잡도가 증가하면:
- **Zustand**: 가볍고 간단
- **Jotai**: Atomic 상태 관리
- **Redux Toolkit**: 엔터프라이즈급 복잡도

### 3. 실시간 협업 기능
- WebSocket 기반 실시간 업데이트
- 여러 사용자가 같은 스레드 동시 접근

### 4. PWA 지원
- Service Worker
- 오프라인 모드
- 푸시 알림

## 참고 자료

- [Next.js App Router 문서](https://nextjs.org/docs/app)
- [React 19 릴리스 노트](https://react.dev/blog/2024/12/05/react-19)
- [Vercel AI SDK](https://sdk.vercel.ai/docs)
- [ShadcN UI](https://ui.shadcn.com/)
- [OpenTelemetry JavaScript](https://opentelemetry.io/docs/languages/js/)
