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
      streamApi('/api/agent/events', { signal: controller.signal }, ({ event }) => {
        if (event === 'run_updated') {
          queryClient.invalidateQueries({ queryKey: ['pending-runs'] });
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
