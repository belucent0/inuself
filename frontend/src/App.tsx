import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { AppRouter } from '@/routes'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000, // 30초간 fresh 상태 유지
      gcTime: 5 * 60 * 1000, // 5분간 캐시 유지
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRouter />
      <Toaster position="top-center" richColors />
    </QueryClientProvider>
  )
}

export default App
