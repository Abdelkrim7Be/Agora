import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentInstancesQuery } from '../../api/queries';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

export default function PlatformLayout() {
  const { globalRole } = useAuth();
  const location = useLocation();
  const instancesQuery = useAgentInstancesQuery();
  const isAdmin = globalRole === 'admin';
  const isAccountRoute = location.pathname === '/account';

  if (!isAdmin && !isAccountRoute) {
    const instances = instancesQuery.data || [];
    const first = instances.find((instance) => String(instance.status || 'active').toLowerCase() !== 'deleted') || instances[0];
    if (first?.id) return <Navigate to={`/instance/${first.id}`} replace />;
    if (!instancesQuery.isLoading) return <Navigate to="/account" replace />;
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
