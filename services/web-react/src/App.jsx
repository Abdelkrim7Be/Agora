import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { InstanceProvider } from './contexts/InstanceContext';
import { I18nProvider } from './contexts/I18nContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PlatformLayout from './components/layout/PlatformLayout';
import LoginPage from './pages/LoginPage';

const queryClient = new QueryClient();

// Placeholder pages — real content lands view by view in later slices.
const WorkspaceLayout = () => <div>Workspace Layout</div>;
const InstancesPage = () => <div>Instances Page</div>;
const AgentTypesPage = () => <div>Agent Types Page</div>;
const HealthPage = () => <div>Health Page</div>;
const AuditPage = () => <div>Audit Page</div>;
const UsersPage = () => <div>Users Page</div>;
const AccountPage = () => <div>Account Page</div>;

const ValidationPage = () => <div>Validation Page</div>;
const DraftsPage = () => <div>Drafts Page</div>;
const InboxPage = () => <div>Inbox Page</div>;

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
              <SessionBodyClass />
              <BrowserRouter>
                <Routes>
                  <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />

                  {/* Platform views */}
                  <Route path="/" element={<ProtectedRoute><PlatformLayout /></ProtectedRoute>}>
                    <Route index element={<InstancesPage />} />
                    <Route path="agent-types" element={<AgentTypesPage />} />
                    <Route path="health" element={<HealthPage />} />
                    <Route path="audit" element={<AuditPage />} />
                    <Route path="users" element={<UsersPage />} />
                    <Route path="account" element={<AccountPage />} />
                  </Route>

                  {/* Workspace views */}
                  <Route path="/instance/:instanceId" element={<ProtectedRoute><WorkspaceLayout /></ProtectedRoute>}>
                    <Route index element={<ValidationPage />} />
                    <Route path="drafts" element={<DraftsPage />} />
                    <Route path="inbox" element={<InboxPage />} />
                    {/* We will add more routes here as we migrate them */}
                  </Route>
                </Routes>
              </BrowserRouter>
            </ThemeProvider>
          </I18nProvider>
        </InstanceProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
