import { useEffect } from 'react';
import { useParams, Outlet } from 'react-router-dom';
import WorkspaceSidebar from './WorkspaceSidebar';
import Topbar from './Topbar';
import { useInstance } from '../../contexts/InstanceContext';

export default function WorkspaceLayout() {
  const { instanceId: paramId } = useParams();
  const { setInstanceId } = useInstance();

  useEffect(() => {
    document.body.classList.add('workspace-mode');
    return () => document.body.classList.remove('workspace-mode');
  }, []);

  useEffect(() => {
    if (paramId) setInstanceId(paramId);
  }, [paramId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="app-shell">
      <WorkspaceSidebar />
      <main className="workspace">
        <Topbar />
        <div className="content-stage">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
