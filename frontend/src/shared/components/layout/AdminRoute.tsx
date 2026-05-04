import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/shared/contexts'

export function AdminRoute() {
  const { user } = useAuth()
  const location = useLocation()

  if (!user?.is_super) {
    return <Navigate to="/" replace state={{ from: location }} />
  }

  return <Outlet />
}
