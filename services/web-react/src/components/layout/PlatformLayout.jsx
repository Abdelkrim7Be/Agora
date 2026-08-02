import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

export default function PlatformLayout() {
  const { globalRole } = useAuth();
  const location = useLocation();
  const isAdmin = globalRole === 'admin';
  const userAllowedRoutes = new Set(['/', '/agent-types', '/account']);

  if (!isAdmin && !userAllowedRoutes.has(location.pathname)) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="workspace">
        <Topbar />
        <div className="content-stage">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
