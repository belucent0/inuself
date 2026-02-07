import { Toaster } from 'sonner'
import { AppRouter } from '@/routes'

function App() {
  return (
    <>
      <AppRouter />
      <Toaster position="top-center" richColors />
    </>
  )
}

export default App
