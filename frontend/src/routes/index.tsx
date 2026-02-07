/**
 * React Router 설정
 */

import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { HomePage, ChatPage, ContentsPage, ContentDetailPage, UploadPage, MonitoringPage, RoadmapPage } from '@/pages'
import { RootLayout } from '@/shared/components/layout'

const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      {
        index: true,
        element: <HomePage />,
      },
      {
        path: 'chat/:threadId',
        element: <ChatPage />,
      },
      {
        path: 'contents',
        element: <ContentsPage />,
      },
      {
        path: 'contents/:id',
        element: <ContentDetailPage />,
      },
      {
        path: 'upload',
        element: <UploadPage />,
      },
      {
        path: 'monitoring',
        element: <MonitoringPage />,
      },
      {
        path: 'roadmap',
        element: <RoadmapPage />,
      },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}
