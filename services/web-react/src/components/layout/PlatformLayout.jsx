import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import { DataErrorBanner } from '../ui/DataErrorBanner';

export default function PlatformLayout() {
  const { globalRole } = useAuth();
  const location = useLocation();
  const isAdmin = globalRole === 'admin';
  const userAllowedRoutes = new Set(['/', '/instances', '/agent-types', '/account']);

  if (!isAdmin && !userAllowedRoutes.has(location.pathname)) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="workspace">
        <Topbar />
        <div className="content-stage">
          <DataErrorBanner />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
