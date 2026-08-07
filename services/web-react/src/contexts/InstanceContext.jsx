import { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { roleAtLeast } from '../utils/roles';
import { clearFailuresForInstance } from '../api/failureLog';

const InstanceContext = createContext(null);

export function isInstanceActive(instance) {
  return String(instance?.status || 'active').toLowerCase() !== 'inactive';
}

export function InstanceProvider({ children }) {
  // No invented id. This used to start at the literal 'default-email-agent',
  // which is only the *seeded* instance's name — any deployment whose instances
  // carry other ids sent that header on every request before the list loaded,
  // got 403 from the gateway, and raised the "reserved for another role" banner
  // on the landing page. The banner then stayed up forever: the failure was
  // recorded under a query key containing the bogus id, and the retry that
  // succeeded ran under a different key, so nothing ever cleared it.
  //
  // Empty means "send no instance header" and the gateway applies its own
  // configured default; the effect below adopts a real instance as soon as the
  // list arrives.
  const [instanceId, setInstanceId] = useState(() => localStorage.getItem('agora.agentInstanceId') || '');
  
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
    if (!fallback) return;
    // Anything recorded against the instance we are leaving belongs to a query
    // key nothing will retry, so it would sit in the banner permanently.
    clearFailuresForInstance(instanceId);
    setInstanceId(fallback.id);
  }, [instances]); // eslint-disable-line react-hooks/exhaustive-deps

  const currentInstanceRole = currentInstance?.effective_role || "viewer";

  const hasRole = (minimum) => roleAtLeast(currentInstanceRole, minimum);

  return (
    <InstanceContext.Provider value={{ instanceId, setInstanceId, instances, setInstances, currentInstance, currentInstanceRole, hasRole }}>
      {children}
    </InstanceContext.Provider>
  );
}

export const useInstance = () => useContext(InstanceContext);
