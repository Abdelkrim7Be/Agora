import { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { useAuth } from './AuthContext';

const InstanceContext = createContext(null);

const ROLE_RANK = { viewer: 1, approver: 2, owner: 3, admin: 4 };

export function InstanceProvider({ children }) {
  const [instanceId, setInstanceId] = useState(() => localStorage.getItem('agora.agentInstanceId') || 'default-email-agent');
  const { globalRole } = useAuth();
  
  // We'll hydrate this from a React Query hook later via a setter or by passing it in.
  // For now, let's keep it simple. The instances will be fetched by a hook that uses this context.
  const [instances, setInstances] = useState([]);

  useEffect(() => {
    if (instanceId) {
      localStorage.setItem('agora.agentInstanceId', instanceId);
    }
  }, [instanceId]);

  const currentInstance = useMemo(() => {
    return instances.find((instance) => instance.id === instanceId) || null;
  }, [instances, instanceId]);

  const currentInstanceRole = currentInstance?.effective_role || globalRole || "viewer";

  const hasRole = (minimum) => {
    return (ROLE_RANK[currentInstanceRole] || 0) >= (ROLE_RANK[minimum] || 99);
  };

  return (
    <InstanceContext.Provider value={{ instanceId, setInstanceId, instances, setInstances, currentInstance, currentInstanceRole, hasRole }}>
      {children}
    </InstanceContext.Provider>
  );
}

export const useInstance = () => useContext(InstanceContext);
