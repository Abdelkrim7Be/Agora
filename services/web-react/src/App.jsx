import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { InstanceProvider } from './contexts/InstanceContext';
import { I18nProvider } from './contexts/I18nContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { StatusProvider } from './contexts/StatusContext';
import { DialogProvider } from './contexts/DialogContext';
import { BusyProvider } from './contexts/BusyContext';
import { ToastProvider } from './contexts/ToastContext';
import { PendingClick } from './components/ui/PendingClick';
import { RequireGlobalRole } from './components/layout/RequireGlobalRole';
import { QueryClient, QueryCache, MutationCache, QueryClientProvider } from '@tanstack/react-query';
import { recordFailure, clearFailure, recordActionFailure, clearActionFailure } from './api/failureLog';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
import PlatformLayout from './components/layout/PlatformLayout';
import WorkspaceLayout from './components/layout/WorkspaceLayout';
import LoginPage from './pages/LoginPage';
import InviteSetupPage from './pages/InviteSetupPage';
import PlatformDashboardPage from './pages/platform/PlatformDashboardPage';
import InstancesPage from './pages/platform/InstancesPage';
import AgentTypesPage from './pages/platform/AgentTypesPage';
import HealthPage from './pages/platform/HealthPage';
import AuditPage from './pages/platform/AuditPage';
import UsersPage from './pages/platform/UsersPage';
import AccountPage from './pages/platform/AccountPage';
import ValidationPage from './pages/workspace/ValidationPage';
import InboxPage from './pages/workspace/InboxPage';
import RunDetailPage from './pages/workspace/RunDetailPage';
import GmailPage from './pages/workspace/GmailPage';
import PersonaPage from './pages/workspace/PersonaPage';
import StylePage from './pages/workspace/StylePage';
import SignaturePage from './pages/workspace/SignaturePage';
import MemoryPage from './pages/workspace/MemoryPage';
import CapabilitiesPage from './pages/workspace/CapabilitiesPage';
import DashboardPage from './pages/workspace/DashboardPage';
import CostsPage from './pages/workspace/CostsPage';
import RolesPage from './pages/workspace/RolesPage';
import ContactsPage from './pages/workspace/ContactsPage';
import SegmentsPage from './pages/workspace/SegmentsPage';
import PermissionsPage from './pages/workspace/PermissionsPage';
import CategoriesPage from './pages/workspace/CategoriesPage';
import RulesPage from './pages/workspace/RulesPage';
import CampaignsPage from './pages/workspace/CampaignsPage';
import DlqPage from './pages/workspace/DlqPage';
import SetupPage from './pages/workspace/SetupPage';
import GuidePage from './pages/workspace/GuidePage';
import JunkPage from './pages/workspace/JunkPage';
import NotificationsPage from './pages/workspace/NotificationsPage';
import OAuthCallbackPage from './pages/OAuthCallbackPage';
import NotFoundPage from './pages/NotFoundPage';

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => recordFailure(query.queryKey, error),
    onSuccess: (_data, query) => clearFailure(query.queryKey),
  }),
  // Reads reported their failures and writes did not, so a button the server
  // declines did nothing visible at all — same as a broken one.
  mutationCache: new MutationCache({
    onError: (error) => recordActionFailure(error),
    onSuccess: () => clearActionFailure(),
  }),
  defaultOptions: {
    queries: {
      // A 401/403/404 is an answer, not a hiccup. Retrying them three times only
      // delays the error the person needs to see.
      retry: (failureCount, error) => {
        const message = error?.message || '';
        if (message.includes('Interdit') || message.includes('Session expiree')) return false;
        return failureCount < 2;
      },
    },
  },
});

function ProtectedRoute({ children }) {
  const { token } = useAuth();
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function AdminOnly({ children }) {
  const { globalRole } = useAuth();
  if (globalRole !== 'admin') return <Navigate to="/instances" replace />;
  return children;
}

function PublicOnlyRoute({ children }) {
  const { token } = useAuth();
  if (token) {
    return <Navigate to="/" replace />;
  }
  return children;
}

// Keeps `body.signed-in` in sync so the CSS written for the vanilla app
// (login layout, pulse-dot colors, etc.) applies unchanged.
function SessionBodyClass() {
  const { token } = useAuth();
  useEffect(() => {
    document.body.classList.toggle('signed-in', Boolean(token));
  }, [token]);
  return null;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <InstanceProvider>
          <I18nProvider>
            <ThemeProvider>
              <StatusProvider>
                <DialogProvider>
                  <ToastProvider>
                  <BusyProvider>
                  <PendingClick />
                  <SessionBodyClass />
                  <BrowserRouter>
                    <ErrorBoundary>
                    <Routes>
                      <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />
                      <Route path="/invite/:token" element={<InviteSetupPage />} />
                      <Route path="/oauth/gmail/callback" element={<OAuthCallbackPage />} />
                      <Route path="/oauth/outlook/callback" element={<OAuthCallbackPage />} />

                      {/* Platform views */}
                      <Route path="/" element={<ProtectedRoute><PlatformLayout /></ProtectedRoute>}>
                        {/* Instance owners work inside a workspace; the platform-wide
                            overview is an administration view. */}
                        <Route index element={<AdminOnly><PlatformDashboardPage /></AdminOnly>} />
                        <Route path="instances" element={<InstancesPage />} />
                        <Route path="agent-types" element={<AgentTypesPage />} />
                        {/* Route segments below deliberately avoid "health"/"audit"/"users" as a
                            leading path segment: nginx.conf (and the dev proxy mirroring it) proxy
                            bare /health, /audit, /users straight to the gateway, so a client route
                            with the same name would break on hard reload / direct navigation. */}
                        <Route path="system-health" element={<HealthPage />} />
                        <Route path="access-log" element={<AuditPage />} />
                        <Route path="team" element={<UsersPage />} />
                        <Route path="account" element={<AccountPage />} />
                      </Route>

                      {/* Workspace views (per instance) */}
                      <Route path="/instance/:instanceId" element={<ProtectedRoute><WorkspaceLayout /></ProtectedRoute>}>
                        <Route index element={<DashboardPage />} />
                        <Route path="setup" element={<SetupPage />} />
                        <Route path="guide" element={<GuidePage />} />
                        <Route path="junk" element={<JunkPage />} />
                        <Route path="notifications" element={<NotificationsPage />} />
                        <Route path="validation" element={<ValidationPage />} />
                        {/* Brouillons and Validation listed the same pending_approval runs
                            through two endpoints — one queue, one page. */}
                        <Route path="drafts" element={<Navigate to="../validation" replace />} />
                        <Route path="inbox" element={<InboxPage />} />
                        <Route path="sent" element={<InboxPage initialMailbox="sent" />} />
                        <Route path="run/:runId" element={<RunDetailPage />} />
                        <Route path="gmail" element={<GmailPage />} />
                        <Route path="config" element={<PersonaPage />} />
                        {/* The name the agent declares for this section in its
                            manifest. `config` predates the contract and is kept
                            so existing links and bookmarks still resolve. */}
                        <Route path="persona" element={<PersonaPage />} />
                        <Route path="style" element={<StylePage />} />
                        <Route path="signature" element={<SignaturePage />} />
                        <Route path="categories" element={<CategoriesPage />} />
                        <Route path="roles" element={<RolesPage />} />
                        <Route path="contacts" element={<ContactsPage />} />
                        <Route path="segments" element={<SegmentsPage />} />
                        <Route path="campaigns" element={<CampaignsPage />} />
                        <Route path="memory" element={<MemoryPage />} />
                        <Route path="rules" element={<RulesPage />} />
                        <Route path="capabilities" element={<RequireGlobalRole view="capabilities"><CapabilitiesPage /></RequireGlobalRole>} />
                        <Route path="permissions" element={<RequireGlobalRole view="permissions"><PermissionsPage /></RequireGlobalRole>} />
                        <Route path="dlq" element={<RequireGlobalRole view="dlq"><DlqPage /></RequireGlobalRole>} />
                        <Route path="costs" element={<RequireGlobalRole view="costs"><CostsPage /></RequireGlobalRole>} />
                      </Route>

                      {/* A mistyped or stale link used to render a blank page. */}
                      <Route path="*" element={<NotFoundPage />} />
                    </Routes>
                    </ErrorBoundary>
                  </BrowserRouter>
                  </BusyProvider>
                  </ToastProvider>
                </DialogProvider>
              </StatusProvider>
            </ThemeProvider>
          </I18nProvider>
        </InstanceProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
