import React from 'react';
import { BrowserRouter, Routes, Route, Outlet, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { InstanceProvider } from './contexts/InstanceContext';
import { I18nProvider } from './contexts/I18nContext';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient();

// Placeholder Layouts and Pages
const PlatformLayout = () => <div>Platform Layout <Outlet /></div>;
const WorkspaceLayout = () => <div>Workspace Layout <Outlet /></div>;

const LoginPage = () => <div>Login Page</div>;
const InstancesPage = () => <div>Instances Page</div>;
const AgentTypesPage = () => <div>Agent Types Page</div>;
const HealthPage = () => <div>Health Page</div>;
const AuditPage = () => <div>Audit Page</div>;
const UsersPage = () => <div>Users Page</div>;
const AccountPage = () => <div>Account Page</div>;

const ValidationPage = () => <div>Validation Page</div>;
const DraftsPage = () => <div>Drafts Page</div>;
const InboxPage = () => <div>Inbox Page</div>;

const ProtectedRoute = ({ children }) => {
  const { token } = useAuth();
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return children;
};

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <InstanceProvider>
          <I18nProvider>
            <BrowserRouter>
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                
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
          </I18nProvider>
        </InstanceProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
