import { useEffect } from 'react';
import { useParams, Outlet } from 'react-router-dom';
import WorkspaceSidebar from './WorkspaceSidebar';
import Topbar from './Topbar';
import { useInstance } from '../../contexts/InstanceContext';
import { useAgentInstancesQuery } from '../../api/queries';

export default function WorkspaceLayout() {
  const { instanceId: paramId } = useParams();
  const { setInstanceId, setInstances } = useInstance();
  // Deep-linking straight into a workspace tab (no prior visit to the Instances
  // page this session) would otherwise leave the sidebar showing the raw id
  // instead of the display name/type — hydrate here too.
  const instancesQuery = useAgentInstancesQuery();

  useEffect(() => {
    document.body.classList.add('workspace-mode');
    return () => document.body.classList.remove('workspace-mode');
  }, []);

  useEffect(() => {
    if (paramId) setInstanceId(paramId);
  }, [paramId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (instancesQuery.data) setInstances(instancesQuery.data);
  }, [instancesQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

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
