import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useApi } from '../api/useApi';
import { useAuth } from '../contexts/AuthContext';

// Real-time nudge for the pending-runs queue: on `run_updated` just invalidate
// the query (matches vanilla — no granular patch, a plain refetch is cheap and
// always correct). The 30s refetchInterval on usePendingRunsQuery is the
// fallback if the stream drops and doesn't reconnect in time.
export function useRunEvents() {
  const { streamApi } = useApi();
  const { token } = useAuth();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!token) return undefined;
    let stopped = false;
    let controller = null;

    const connect = () => {
      if (stopped) return;
      controller = new AbortController();
      streamApi('/api/agent/events', { signal: controller.signal }, ({ event, data }) => {
        if (event === 'run_updated') {
          queryClient.invalidateQueries({ queryKey: ['pending-runs'] });
          queryClient.invalidateQueries({ queryKey: ['notifications'] });
          queryClient.invalidateQueries({ queryKey: ['notifications-unread-count'] });
          if (document.hidden && window.Notification?.permission === 'granted') {
            const title = data?.status === 'pending_approval' ? 'Brouillon prêt à valider' : 'Activité de l’agent';
            const body = [data?.subject, data?.author].filter(Boolean).join(' · ');
            new Notification(title, { body: body || 'Agora a mis à jour une exécution.' });
          }
        }
      })
        .catch(() => {})
        .finally(() => {
          if (!stopped) setTimeout(connect, 3000);
        });
    };
    connect();

    return () => {
      stopped = true;
      controller?.abort();
    };
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps
}
