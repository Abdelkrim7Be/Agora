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

const queryClient = new QueryClient();

// Placeholder pages — real content lands tab by tab in later slices.
const DashboardPage = () => <div>Tableau de bord</div>;
const DraftsPage = () => <div>Brouillons</div>;
const InboxPage = () => <div>Messages</div>;
const GmailPage = () => <div>Synchronisation Gmail</div>;
const ConfigPage = () => <div>Persona</div>;
const StylePage = () => <div>Style</div>;
const SignaturePage = () => <div>Signature</div>;
const CategoriesPage = () => <div>Workflows</div>;
const RolesPage = () => <div>Annuaire des rôles</div>;
const ContactsPage = () => <div>Contacts</div>;
const SegmentsPage = () => <div>Segments</div>;
const CampaignsPage = () => <div>Campagnes</div>;
const MemoryPage = () => <div>Mémoire</div>;
const RulesPage = () => <div>Règles</div>;
const CapabilitiesPage = () => <div>Capacités</div>;
const PermissionsPage = () => <div>Permissions</div>;
const DlqPage = () => <div>DLQ</div>;
const CostsPage = () => <div>Coûts</div>;

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
                        <Route path="validation" element={<ValidationPage />} />
                        <Route path="drafts" element={<DraftsPage />} />
                        <Route path="inbox" element={<InboxPage />} />
                        <Route path="gmail" element={<GmailPage />} />
                        <Route path="config" element={<ConfigPage />} />
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
