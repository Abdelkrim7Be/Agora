import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useApi } from './useApi';
import { useAuth } from '../contexts/AuthContext';
import { useInstance } from '../contexts/InstanceContext';

const PENDING_PAGE_SIZE = 50;

export function useAgentInstancesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  return useQuery({
    queryKey: ['agent-instances'],
    queryFn: () => api('/agent-instances'),
    enabled: Boolean(token),
  });
}

export function useAgentTypesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  return useQuery({
    queryKey: ['agents'],
    queryFn: () => api('/agents'),
    enabled: Boolean(token),
  });
}

export function useHealthQuery(period) {
  const { api } = useApi();
  const { token } = useAuth();
  return useQuery({
    queryKey: ['health', period],
    queryFn: async () => {
      const [health, activity] = await Promise.all([
        api('/api/agent/health'),
        api(`/api/agent/analytics?period=${encodeURIComponent(period)}`).catch(() => null),
      ]);
      return { health, activity };
    },
    enabled: Boolean(token),
  });
}

export function useAuditQuery(page, limit) {
  const { api } = useApi();
  const { token } = useAuth();
  const fetchSize = Math.min(limit + 1, 500);
  return useQuery({
    queryKey: ['audit', page, limit],
    queryFn: async () => {
      const events = await api(`/audit?limit=${encodeURIComponent(fetchSize)}&page=${page}`);
      return { events: events.slice(0, limit), hasMore: events.length > limit };
    },
    enabled: Boolean(token),
  });
}

export function useUsersQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  return useQuery({
    queryKey: ['users'],
    queryFn: () => api('/users'),
    enabled: Boolean(token),
  });
}

export function useRenameInstance() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, displayName }) => api(`/agent-instances/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ display_name: displayName }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agent-instances'] }),
  });
}

export function useDeleteInstance() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => api(`/agent-instances/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agent-instances'] }),
  });
}

export function useCreateUser() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => api('/users', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useSetUserEnabled() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }) => api(`/users/${encodeURIComponent(id)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

// --- Validation (pending-approval queue) ---

export function usePendingRunsQuery(page) {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['pending-runs', instanceId, page],
    queryFn: () => api(`/api/agent/runs?status=pending_approval&limit=${PENDING_PAGE_SIZE}&offset=${page * PENDING_PAGE_SIZE}`),
    enabled: Boolean(token),
    refetchInterval: 30_000,
  });
}

function invalidatePendingRuns(queryClient) {
  return queryClient.invalidateQueries({ queryKey: ['pending-runs'] });
}

export function useApproveRun() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, args }) => api(`/api/agent/run/${runId}/approve`, {
      method: 'POST',
      body: JSON.stringify(args ? { args } : {}),
    }),
    onSuccess: () => invalidatePendingRuns(queryClient),
  });
}

export function useRejectRun() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId) => api(`/api/agent/run/${runId}/reject`, { method: 'POST' }),
    onSuccess: () => invalidatePendingRuns(queryClient),
  });
}

export function useClaimRun() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId) => api(`/api/agent/inbox/${runId}/claim`, { method: 'POST' }),
    onSuccess: () => invalidatePendingRuns(queryClient),
  });
}

export function useAssignRun() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, assignee }) => api(`/api/agent/inbox/${runId}/assign`, {
      method: 'POST',
      body: JSON.stringify({ assignee }),
    }),
    onSuccess: () => invalidatePendingRuns(queryClient),
  });
}

export function useSummarizeRun() {
  const { api } = useApi();
  return useMutation({
    mutationFn: (runId) => api(`/api/agent/run/${runId}/summarize`, { method: 'POST' }),
  });
}

export function useToneRun() {
  const { api } = useApi();
  return useMutation({
    mutationFn: ({ runId, tone }) => api(`/api/agent/run/${runId}/tone`, {
      method: 'POST',
      body: JSON.stringify({ tone }),
    }),
  });
}

export function useBulkDecision() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runIds, decision }) => api('/api/agent/runs/bulk', {
      method: 'POST',
      body: JSON.stringify({ run_ids: runIds, decision }),
    }),
    onSuccess: () => invalidatePendingRuns(queryClient),
  });
}

export function useSyncGmail() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api('/api/agent/sync?limit=50', { method: 'POST' }),
    onSuccess: () => invalidatePendingRuns(queryClient),
  });
}
