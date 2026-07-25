import { useEffect } from 'react';
import { useParams, useLocation, Outlet } from 'react-router-dom';
import WorkspaceSidebar from './WorkspaceSidebar';
import Topbar from './Topbar';
import SetupPage from '../../pages/workspace/SetupPage';
import { useInstance } from '../../contexts/InstanceContext';
import { useAgentInstancesQuery, useInstanceSetupQuery } from '../../api/queries';

export default function WorkspaceLayout() {
  const { instanceId: paramId } = useParams();
  const location = useLocation();
  const { setInstanceId, setInstances } = useInstance();
  // Deep-linking straight into a workspace tab (no prior visit to the Instances
  // page this session) would otherwise leave the sidebar showing the raw id
  // instead of the display name/type — hydrate here too.
  const instancesQuery = useAgentInstancesQuery();
  const setupQuery = useInstanceSetupQuery();

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

  // 'not_started' instances predate the onboarding pipeline (or opted out via
  // skip) and must never be locked out of their existing workspace. Only a
  // setup that has actually started and isn't finished yet gates the tabs.
  const setupStatus = setupQuery.data?.status;
  const isGated = setupStatus && setupStatus !== 'ready' && setupStatus !== 'not_started';
  const onSetupRoute = location.pathname.endsWith('/setup');

  return (
    <div className="app-shell">
      <WorkspaceSidebar />
      <main className="workspace">
        <Topbar />
        <div className="content-stage">
          {isGated && !onSetupRoute ? <SetupPage /> : <Outlet />}
        </div>
      </main>
    </div>
  );
}
