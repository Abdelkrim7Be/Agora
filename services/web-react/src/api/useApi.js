import { useAuth } from '../contexts/AuthContext';
import { useInstance } from '../contexts/InstanceContext';
import { api, streamApi } from './client';

// A wrapper hook to inject auth context automatically
export function useApi() {
  const { gatewayBase, token, signOut } = useAuth();
  const { instanceId } = useInstance();

  const fetchApi = (path, options) => {
    return api(gatewayBase, token, instanceId, signOut, path, options);
  };

  const fetchStreamApi = (path, options, onEvent) => {
    return streamApi(gatewayBase, token, instanceId, signOut, path, options, onEvent);
  };

  return { api: fetchApi, streamApi: fetchStreamApi };
}
