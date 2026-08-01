import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { InstanceProvider } from './contexts/InstanceContext';
import { I18nProvider } from './contexts/I18nContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { StatusProvider } from './contexts/StatusContext';
import { DialogProvider } from './contexts/DialogContext';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PlatformLayout from './components/layout/PlatformLayout';
import WorkspaceLayout from './components/layout/WorkspaceLayout';
import LoginPage from './pages/LoginPage';
import InstancesPage from './pages/platform/InstancesPage';
import AgentTypesPage from './pages/platform/AgentTypesPage';
import HealthPage from './pages/platform/HealthPage';
import AuditPage from './pages/platform/AuditPage';
import UsersPage from './pages/platform/UsersPage';
import AccountPage from './pages/platform/AccountPage';
import ValidationPage from './pages/workspace/ValidationPage';
import DraftsPage from './pages/workspace/DraftsPage';
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

const queryClient = new QueryClient();

// Placeholder pages — real content lands tab by tab in later slices.

function ProtectedRoute({ children }) {
  const { token } = useAuth();
  if (!token) {
    return <Navigate to="/login" replace />;
  }
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
                  <SessionBodyClass />
                  <BrowserRouter>
                    <Routes>
                      <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />
                      <Route path="/oauth/gmail/callback" element={<OAuthCallbackPage />} />

                      {/* Platform views */}
                      <Route path="/" element={<ProtectedRoute><PlatformLayout /></ProtectedRoute>}>
                        <Route index element={<InstancesPage />} />
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
                        <Route path="drafts" element={<DraftsPage />} />
                        <Route path="inbox" element={<InboxPage />} />
                        <Route path="run/:runId" element={<RunDetailPage />} />
                        <Route path="gmail" element={<GmailPage />} />
                        <Route path="config" element={<PersonaPage />} />
                        <Route path="style" element={<StylePage />} />
                        <Route path="signature" element={<SignaturePage />} />
                        <Route path="categories" element={<CategoriesPage />} />
                        <Route path="roles" element={<RolesPage />} />
                        <Route path="contacts" element={<ContactsPage />} />
                        <Route path="segments" element={<SegmentsPage />} />
                        <Route path="campaigns" element={<CampaignsPage />} />
                        <Route path="memory" element={<MemoryPage />} />
                        <Route path="rules" element={<RulesPage />} />
                        <Route path="capabilities" element={<CapabilitiesPage />} />
                        <Route path="permissions" element={<PermissionsPage />} />
                        <Route path="dlq" element={<DlqPage />} />
                        <Route path="costs" element={<CostsPage />} />
                      </Route>
                    </Routes>
                  </BrowserRouter>
                </DialogProvider>
              </StatusProvider>
            </ThemeProvider>
          </I18nProvider>
        </InstanceProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
