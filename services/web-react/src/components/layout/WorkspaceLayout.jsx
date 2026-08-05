import { useEffect } from 'react';
import { useParams, useLocation, useNavigate, Outlet } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import WorkspaceSidebar from './WorkspaceSidebar';
import Topbar from './Topbar';
import SetupPage from '../../pages/workspace/SetupPage';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { inboxQueryKey, inboxQueryPath, useAgentInstancesQuery, useInstanceSetupQuery } from '../../api/queries';
import { useApi } from '../../api/useApi';
import { DataErrorBanner } from '../ui/DataErrorBanner';

export default function WorkspaceLayout() {
  const { instanceId: paramId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { setInstanceId, setInstances } = useInstance();
  const { setStatus } = useStatus();
  const queryClient = useQueryClient();
  const { api } = useApi();
  // Deep-linking straight into a workspace tab (no prior visit to the Instances
  // page this session) would otherwise leave the sidebar showing the raw id
  // instead of the display name/type — hydrate here too.
  const instancesQuery = useAgentInstancesQuery();
  const setupQuery = useInstanceSetupQuery();
  const setupStatus = setupQuery.data?.status;

  useEffect(() => {
    document.body.classList.add('workspace-mode');
    return () => document.body.classList.remove('workspace-mode');
  }, []);

  // Gmail connect completes in the popup (Google -> our callback -> app).
  // If the callback ever lands in this workspace window, consume the result and
  // refresh setup/status data; otherwise the main setup page learns completion
  // from useGmailStatusQuery polling.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const gmailResult = params.get('gmail');
    if (!gmailResult) return;
    if (gmailResult === 'connected') {
      setStatus('Gmail connecté.', 'ok');
      queryClient.invalidateQueries({ queryKey: ['instance-setup'] });
      queryClient.invalidateQueries({ queryKey: ['gmail-status'] });
      queryClient.invalidateQueries({ queryKey: ['agent-instances'] });
    } else {
      setStatus(`Connexion Gmail échouée : ${params.get('message') || 'erreur inconnue'}`, 'error');
    }
    params.delete('gmail');
    params.delete('message');
    const query = params.toString();
    navigate({ pathname: location.pathname, search: query ? `?${query}` : '' }, { replace: true });
  }, [location.search]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const handleOAuthComplete = (payload) => {
      if (!payload || payload.type !== "agora:gmail-oauth-complete") return;
      if (payload.agentInstanceId && paramId && payload.agentInstanceId !== paramId) return;
      if (payload.status === "connected") {
        setStatus("Gmail connecté.", "ok");
        queryClient.invalidateQueries({ queryKey: ["instance-setup"] });
        queryClient.invalidateQueries({ queryKey: ["gmail-status"] });
        queryClient.invalidateQueries({ queryKey: ["agent-instances"] });
      } else {
        setStatus("Connexion Gmail échouée : " + (payload.message || "erreur inconnue"), "error");
      }
    };
    const handleMessage = (event) => {
      if (event.origin !== window.location.origin) return;
      handleOAuthComplete(event.data);
    };
    const handleStorage = (event) => {
      if (event.key !== "agora:gmail-oauth-complete" || !event.newValue) return;
      try {
        handleOAuthComplete(JSON.parse(event.newValue));
      } catch (_error) {
        // Ignore malformed storage events from older tabs.
      }
    };
    window.addEventListener("message", handleMessage);
    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("message", handleMessage);
      window.removeEventListener("storage", handleStorage);
    };
  }, [paramId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (paramId) setInstanceId(paramId);
  }, [paramId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (instancesQuery.data) setInstances(instancesQuery.data);
  }, [instancesQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // An email workspace is not usable until Gmail connection and minimum setup
  // are complete. Gate not_started/loading states too so users do not land on
  // empty dashboards while onboarding has not populated inbox, contacts,
  // categories, persona/style, and initial drafts.
  const isGated = setupQuery.isLoading || setupQuery.isError || setupStatus !== 'ready';
  const onSetupRoute = location.pathname.endsWith('/setup');

  useEffect(() => {
    if (setupStatus !== 'ready' || !paramId) return;
    [
      ['inbox', paramId],
      ['categories', paramId],
      ['contacts', paramId],
      ['persona', paramId],
      ['style', paramId],
      ['memory-summary', paramId],
      ['memory', paramId],
      ['analytics', paramId],
      ['gmail-status', paramId],
      ['notifications', paramId],
      ['notifications-unread-count', paramId],
      ['pending-runs', paramId],
    ].forEach((queryKey) => queryClient.invalidateQueries({ queryKey }));
    queryClient.invalidateQueries({ queryKey: ['agent-instances'] });
    queryClient.prefetchQuery({
      queryKey: inboxQueryKey(paramId, 'inbox'),
      queryFn: () => api(inboxQueryPath('inbox')),
      staleTime: 60_000,
    });
  }, [setupStatus, paramId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="app-shell">
      <WorkspaceSidebar />
      <main className="workspace">
        <Topbar />
        <div className="content-stage">
          <DataErrorBanner />
          {isGated && !onSetupRoute ? <SetupPage /> : <Outlet />}
        </div>
      </main>
    </div>
  );
}
