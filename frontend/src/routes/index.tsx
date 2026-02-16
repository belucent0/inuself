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
  ScanPage,
  WpiTestPage,
  WpiResultPage,
  ScanHistoryPage,
  ScanDetailPage,
} from '@/pages'
import { RootLayout } from '@/shared/components/layout'

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
      // Scan routes
      {
        path: 'scan',
        element: <ScanPage />,
      },
      {
        path: 'scan/wpi',
        element: <WpiTestPage />,
      },
      {
        path: 'scan/wpi/result',
        element: <WpiResultPage />,
      },
      {
        path: 'scan/history',
        element: <ScanHistoryPage />,
      },
      {
        path: 'scan/history/:resultId',
        element: <ScanDetailPage />,
      },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}
