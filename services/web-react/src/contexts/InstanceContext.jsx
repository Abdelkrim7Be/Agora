import { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { useAuth } from './AuthContext';
import { roleAtLeast } from '../utils/roles';

const InstanceContext = createContext(null);

export function isInstanceActive(instance) {
  return String(instance?.status || 'active').toLowerCase() !== 'inactive';
}

export function InstanceProvider({ children }) {
  const [instanceId, setInstanceId] = useState(() => localStorage.getItem('agora.agentInstanceId') || 'default-email-agent');
  const { globalRole } = useAuth();
  
  // Hydrated by useAgentInstancesQuery's sync effect once it loads.
  const [instances, setInstances] = useState([]);

  useEffect(() => {
    if (instanceId) {
      localStorage.setItem('agora.agentInstanceId', instanceId);
    }
  }, [instanceId]);

  const currentInstance = useMemo(() => {
    return instances.find((instance) => instance.id === instanceId) || null;
  }, [instances, instanceId]);

  // Mirrors the vanilla app: if the selected instance is missing or inactive,
  // fall back to the first active one once the list loads.
  useEffect(() => {
    if (!instances.length) return;
    if (currentInstance && isInstanceActive(currentInstance)) return;
    const fallback = instances.find(isInstanceActive);
    if (fallback) setInstanceId(fallback.id);
  }, [instances]); // eslint-disable-line react-hooks/exhaustive-deps

  const currentInstanceRole = currentInstance?.effective_role || globalRole || "viewer";

  const hasRole = (minimum) => roleAtLeast(currentInstanceRole, minimum);

  return (
    <InstanceContext.Provider value={{ instanceId, setInstanceId, instances, setInstances, currentInstance, currentInstanceRole, hasRole }}>
      {children}
    </InstanceContext.Provider>
  );
}

export const useInstance = () => useContext(InstanceContext);
