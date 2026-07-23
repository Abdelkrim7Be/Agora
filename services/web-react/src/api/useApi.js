import { useAuth } from '../contexts/AuthContext';
import { useInstance } from '../contexts/InstanceContext';
import { api, streamApi, apiUpload, apiBlob } from './client';

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

  const fetchUpload = (path, file) => {
    return apiUpload(gatewayBase, token, instanceId, signOut, path, file);
  };

  const fetchBlob = (path) => {
    return apiBlob(gatewayBase, token, instanceId, signOut, path);
  };

  return { api: fetchApi, streamApi: fetchStreamApi, apiUpload: fetchUpload, apiBlob: fetchBlob };
}
