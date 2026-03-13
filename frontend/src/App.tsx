import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { AppRouter } from '@/routes'
import { AuthProvider } from '@/shared/contexts'
import { queryClient } from '@/shared/lib/queryClient'

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AppRouter />
      </AuthProvider>
      <Toaster position="top-center" richColors />
    </QueryClientProvider>
  )
}

export default App
