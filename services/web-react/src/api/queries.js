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

// --- Drafts ---

export function useDraftsQuery(category, priority) {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  const params = new URLSearchParams({ limit: '100' });
  if (category) params.set('category', category);
  if (priority) params.set('priority', priority);
  return useQuery({
    queryKey: ['drafts', instanceId, category, priority],
    queryFn: async () => (await api(`/api/agent/drafts?${params.toString()}`)).drafts || [],
    enabled: Boolean(token),
  });
}

// --- Inbox ---

export function useInboxQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['inbox', instanceId],
    queryFn: () => api(`/api/agent/inbox?limit=${INBOX_PAGE_SIZE}`),
    enabled: Boolean(token),
  });
}

const INBOX_PAGE_SIZE = 25;

export function useInboxAction() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ msgId, command }) => api(`/api/agent/inbox/${msgId}/${command}`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['inbox', instanceId] }),
  });
}

// --- Run Detail ---

export function useRunDetailQuery(runId) {
  const { api } = useApi();
  const { token } = useAuth();
  return useQuery({
    queryKey: ['run-detail', runId],
    queryFn: () => api(`/api/agent/run/${runId}/detail`),
    enabled: Boolean(token && runId),
  });
}

// --- Gmail ---

export function useGmailStatusQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['gmail-status', instanceId],
    queryFn: () => api('/api/agent/sync/status'),
    enabled: Boolean(token),
  });
}

export function useGmailSyncNow() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/sync?limit=20', { method: 'POST' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['gmail-status', instanceId] });
      queryClient.invalidateQueries({ queryKey: ['drafts', instanceId] });
    },
  });
}

export function useGmailPause() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/sync/pause', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gmail-status', instanceId] }),
  });
}

export function useGmailResume() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/sync/resume', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gmail-status', instanceId] }),
  });
}

export function useGmailDisconnect() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/disconnect/gmail', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gmail-status', instanceId] }),
  });
}

// --- Persona ---

export function usePersonaQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['persona', instanceId],
    queryFn: () => api('/api/agent/persona'),
    enabled: Boolean(token),
  });
}

export function useSavePersona() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (persona) => api('/api/agent/persona', { method: 'PUT', body: JSON.stringify(persona) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['config', instanceId] }),
  });
}

export function useSuggestPersona() {
  const { api } = useApi();
  return useMutation({
    mutationFn: () => api('/api/agent/persona/suggest', { method: 'POST' }),
  });
}

// --- Config (advanced raw instruction textareas) ---

export function useConfigQuery(enabled = true) {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['config', instanceId],
    queryFn: () => api('/api/agent/config'),
    enabled: Boolean(token) && enabled,
  });
}

export function useSaveConfig() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (config) => api('/api/agent/config', { method: 'PUT', body: JSON.stringify(config) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['config', instanceId] }),
  });
}

// --- Style ---

export function useStyleQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['style', instanceId],
    queryFn: () => api('/api/agent/style'),
    enabled: Boolean(token),
  });
}

export function useSaveStyle() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (writing_style) => api('/api/agent/style', { method: 'PUT', body: JSON.stringify({ writing_style }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['style', instanceId] }),
  });
}

export function useLearnStyle() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/style/learn', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['style', instanceId] }),
  });
}

export function useClearStyle() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/style', { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['style', instanceId] }),
  });
}

// --- Signature ---

export function useSignatureQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['signature', instanceId],
    queryFn: () => api('/api/agent/signature'),
    enabled: Boolean(token),
  });
}

export function useSaveSignature() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (payload) => api('/api/agent/signature', { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['signature', instanceId] }),
  });
}

export function useUploadSignatureImage() {
  const { apiUpload } = useApi();
  return useMutation({
    mutationFn: (file) => apiUpload('/api/agent/signature/image', file),
  });
}

export function useDeleteSignatureImage() {
  const { api } = useApi();
  return useMutation({
    mutationFn: () => api('/api/agent/signature/image', { method: 'DELETE' }),
  });
}

// --- Memory ---

export function useMemorySummaryQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['memory-summary', instanceId],
    queryFn: () => api('/api/agent/memory/summary'),
    enabled: Boolean(token),
  });
}

export function useMemoryQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['memory', instanceId],
    queryFn: () => api('/api/agent/memory'),
    enabled: Boolean(token),
  });
}

export function useSaveMemory() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (body) => api('/api/agent/memory', { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['memory', instanceId] }),
  });
}

export function useDeleteMemoryItem() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ kind, id }) => api(`/api/agent/memory/item?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['memory-summary', instanceId] }),
  });
}

export function useClearMemory() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: () => api('/api/agent/memory', { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory-summary', instanceId] });
      queryClient.invalidateQueries({ queryKey: ['memory', instanceId] });
    },
  });
}

// --- Capabilities + policy ---

export function useCapabilitiesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['capabilities', instanceId],
    queryFn: () => api('/api/agent/capabilities'),
    enabled: Boolean(token),
  });
}

export function useSaveCapabilities() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (capabilities) => api('/api/agent/capabilities', { method: 'PUT', body: JSON.stringify({ capabilities }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['capabilities', instanceId] }),
  });
}

export function usePolicyQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['policy', instanceId],
    queryFn: () => api('/api/agent/policy'),
    enabled: Boolean(token),
  });
}

// --- Analytics (dashboard) ---

export function useAnalyticsQuery(period) {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['analytics', instanceId, period],
    queryFn: () => api(`/api/agent/analytics?period=${encodeURIComponent(period)}`),
    enabled: Boolean(token),
  });
}

// --- Costs ---

export function useCostsQuery(period) {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['costs', instanceId, period],
    queryFn: async () => {
      const [summary, raw] = await Promise.all([
        api(`/api/agent/costs/summary?period=${encodeURIComponent(period)}`),
        api('/api/agent/costs?limit=100'),
      ]);
      return { summary, entries: raw.costs || [] };
    },
    enabled: Boolean(token),
  });
}

// --- Roles directory ---

export function useRolesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['roles', instanceId],
    queryFn: () => api('/api/agent/roles'),
    enabled: Boolean(token),
  });
}

export function useSaveRole() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ roleKey, payload }) => (roleKey
      ? api(`/api/agent/roles/${encodeURIComponent(roleKey)}`, { method: 'PUT', body: JSON.stringify({ ...payload, role_key: roleKey }) })
      : api('/api/agent/roles', { method: 'POST', body: JSON.stringify(payload) })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roles', instanceId] }),
  });
}

export function useDeleteRole() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (roleKey) => api(`/api/agent/roles/${encodeURIComponent(roleKey)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roles', instanceId] }),
  });
}

// --- Contacts directory ---

export function useContactsQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['contacts', instanceId],
    queryFn: () => api('/api/agent/contacts'),
    enabled: Boolean(token),
  });
}

export function useSaveContact() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ email, payload }) => (email
      ? api(`/api/agent/contacts/${encodeURIComponent(email)}`, { method: 'PUT', body: JSON.stringify({ ...payload, email }) })
      : api('/api/agent/contacts', { method: 'POST', body: JSON.stringify(payload) })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['contacts', instanceId] }),
  });
}

export function useDeleteContact() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (email) => api(`/api/agent/contacts/${encodeURIComponent(email)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['contacts', instanceId] }),
  });
}

export function useImportContacts() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ csvText, audienceDefault }) => api('/api/agent/contacts/import', {
      method: 'POST',
      body: JSON.stringify({ csv_text: csvText, audience_default: audienceDefault }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['contacts', instanceId] }),
  });
}

export function useUploadContactPhoto() {
  const { apiUpload } = useApi();
  return useMutation({
    mutationFn: ({ email, file }) => apiUpload(`/api/agent/contacts/${encodeURIComponent(email)}/photo`, file),
  });
}

export function useDeleteContactPhoto() {
  const { api } = useApi();
  return useMutation({
    mutationFn: (email) => api(`/api/agent/contacts/${encodeURIComponent(email)}/photo`, { method: 'DELETE' }),
  });
}

// --- Segments ---

export function useSegmentsQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['segments', instanceId],
    queryFn: () => api('/api/agent/segments'),
    enabled: Boolean(token),
  });
}

export function useSaveSegment() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ id, payload }) => (id
      ? api(`/api/agent/segments/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ ...payload, id }) })
      : api('/api/agent/segments', { method: 'POST', body: JSON.stringify(payload) })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['segments', instanceId] }),
  });
}

export function useDeleteSegment() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (id) => api(`/api/agent/segments/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['segments', instanceId] }),
  });
}

// --- Permissions (per-instance grants) ---

export function useGrantsQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['grants', instanceId],
    queryFn: () => api(`/agent-instances/${encodeURIComponent(instanceId)}/grants`),
    enabled: Boolean(token) && Boolean(instanceId),
  });
}

export function useAddGrant() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ userId, role }) => api(`/agent-instances/${encodeURIComponent(instanceId)}/grants`, {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, role }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['grants', instanceId] }),
  });
}

export function useRemoveGrant() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (userId) => api(`/agent-instances/${encodeURIComponent(instanceId)}/grants/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['grants', instanceId] }),
  });
}

// --- Categories (workflow builder) ---

export function useCategoriesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['categories', instanceId],
    queryFn: () => api('/api/agent/categories'),
    enabled: Boolean(token),
  });
}

export function useSaveCategoriesYaml() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (categoriesYaml) => api('/api/agent/categories', { method: 'PUT', body: JSON.stringify({ categories_yaml: categoriesYaml }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['categories', instanceId] }),
  });
}

export function useSaveCategoryEdit() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ name, payload }) => api(`/api/agent/categories/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['categories', instanceId] }),
  });
}

export function useDuplicateCategory() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (name) => api(`/api/agent/categories/${encodeURIComponent(name)}/duplicate`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['categories', instanceId] }),
  });
}

export function useDeleteCategory() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (name) => api(`/api/agent/categories/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['categories', instanceId] }),
  });
}

export function useTestCategoryMatch() {
  const { api } = useApi();
  return useMutation({
    mutationFn: ({ author, subject }) => api('/api/agent/categories/test-match', {
      method: 'POST',
      body: JSON.stringify({ author, subject, email_thread: '' }),
    }),
  });
}

// --- Rules ---

export function useRulesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['rules', instanceId],
    queryFn: () => api('/api/agent/rules'),
    enabled: Boolean(token),
  });
}

export function useRuleSuggestionsQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['rule-suggestions', instanceId],
    queryFn: () => api('/api/agent/rules/suggestions'),
    enabled: Boolean(token),
  });
}

function invalidateRules(queryClient, instanceId) {
  queryClient.invalidateQueries({ queryKey: ['rules', instanceId] });
}

export function useSaveRulesYaml() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (rulesYaml) => api('/api/agent/rules', { method: 'PUT', body: JSON.stringify({ rules_yaml: rulesYaml }) }),
    onSuccess: () => invalidateRules(queryClient, instanceId),
  });
}

export function useSaveRule() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (payload) => api('/api/agent/rules/rule', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: () => invalidateRules(queryClient, instanceId),
  });
}

export function useDeleteRule() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (name) => api('/api/agent/rules/rule-delete', { method: 'POST', body: JSON.stringify({ name }) }),
    onSuccess: () => invalidateRules(queryClient, instanceId),
  });
}

export function useToggleRule() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ name, enabled }) => api('/api/agent/rules/rule-toggle', { method: 'POST', body: JSON.stringify({ name, enabled }) }),
    onSuccess: () => invalidateRules(queryClient, instanceId),
  });
}

export function useSaveSectionConfig() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ section, config }) => api('/api/agent/rules/section-config', { method: 'PUT', body: JSON.stringify({ section, config }) }),
    onSuccess: () => invalidateRules(queryClient, instanceId),
  });
}

export function useToggleSection() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ section, enabled }) => api('/api/agent/rules/section-toggle', { method: 'POST', body: JSON.stringify({ section, enabled }) }),
    onSuccess: () => invalidateRules(queryClient, instanceId),
  });
}

export function usePromoteSuggestion() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (index) => api(`/api/agent/rules/suggestions/${encodeURIComponent(index)}/promote`, { method: 'POST' }),
    onSuccess: () => {
      invalidateRules(queryClient, instanceId);
      queryClient.invalidateQueries({ queryKey: ['rule-suggestions', instanceId] });
      queryClient.invalidateQueries({ queryKey: ['categories', instanceId] });
    },
  });
}

export function useDismissSuggestion() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (index) => api(`/api/agent/rules/suggestions/${encodeURIComponent(index)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rule-suggestions', instanceId] }),
  });
}

// --- Campaigns ---

export function useCampaignTemplatesQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['campaign-templates', instanceId],
    queryFn: () => api('/api/agent/campaigns/templates'),
    enabled: Boolean(token),
  });
}

export function useSaveCampaignTemplate() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (payload) => api('/api/agent/campaigns/templates', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['campaign-templates', instanceId] }),
  });
}

export function useDeleteCampaignTemplate() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (name) => api(`/api/agent/campaigns/templates/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['campaign-templates', instanceId] }),
  });
}

export function usePendingCampaignsQuery() {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['campaigns-pending', instanceId],
    queryFn: () => api('/api/agent/campaigns'),
    enabled: Boolean(token),
  });
}

export function useCampaignPreviewQuery(segmentId, templateName) {
  const { api } = useApi();
  const { token } = useAuth();
  const { instanceId } = useInstance();
  return useQuery({
    queryKey: ['campaign-preview', instanceId, segmentId, templateName],
    queryFn: () => api('/api/agent/campaigns/preview', {
      method: 'POST',
      body: JSON.stringify({ segment_id: segmentId, template_name: templateName }),
    }),
    enabled: Boolean(token) && Boolean(segmentId) && Boolean(templateName),
    retry: false,
  });
}

export function usePrepareCampaign() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: ({ segmentId, templateName }) => api('/api/agent/campaigns/prepare', {
      method: 'POST',
      body: JSON.stringify({ segment_id: segmentId, template_name: templateName }),
    }),
    onSuccess: (result, { segmentId, templateName }) => {
      queryClient.invalidateQueries({ queryKey: ['campaigns-pending', instanceId] });
      queryClient.setQueryData(['campaign-preview', instanceId, segmentId, templateName], result);
    },
  });
}

export function useApproveCampaign() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (campaignId) => api(`/api/agent/campaigns/${encodeURIComponent(campaignId)}/approve`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['campaigns-pending', instanceId] }),
  });
}

export function useRejectCampaign() {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const { instanceId } = useInstance();
  return useMutation({
    mutationFn: (campaignId) => api(`/api/agent/campaigns/${encodeURIComponent(campaignId)}/reject`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['campaigns-pending', instanceId] }),
  });
}
