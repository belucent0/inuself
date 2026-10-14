/**
 * React Router 설정
 */

import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import {
  HomePage,
  LoginPage,
  SignupPage,
  ChatPage,
  ContentsPage,
  ContentDetailPage,
  UploadPage,
  MonitoringPage,
  RoadmapPage,
  ThreadsPage,
} from '@/pages'
import { RootLayout, AdminRoute } from '@/shared/components/layout'

const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/signup',
    element: <SignupPage />,
  },
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
        path: 'threads',
        element: <ThreadsPage />,
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
        element: <AdminRoute />,
        children: [
          {
            path: 'monitoring',
            element: <MonitoringPage />,
          },
        ],
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
