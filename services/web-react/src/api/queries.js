import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useApi } from './useApi';
import { useAuth } from '../contexts/AuthContext';

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
