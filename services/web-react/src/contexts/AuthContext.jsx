import { createContext, useContext, useState, useEffect } from 'react';
import { decodeJwtRole } from '../utils/jwt';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('agora.token') || '');
  const [gatewayBase, setGatewayBase] = useState(() => {
    const stored = localStorage.getItem('agora.gatewayBase');
    if (stored === null) return '';
    const legacy = new Set(['8080', '8090'].map((port) => `${window.location.protocol}//${window.location.hostname}:${port}`));
    if (legacy.has(stored.replace(/\/$/, ''))) {
      localStorage.setItem('agora.gatewayBase', '');
      return '';
    }
    return stored;
  });

  const globalRole = token ? decodeJwtRole(token) : '';

  useEffect(() => {
    if (token) {
      localStorage.setItem('agora.token', token);
    } else {
      localStorage.removeItem('agora.token');
    }
  }, [token]);

  useEffect(() => {
    localStorage.setItem('agora.gatewayBase', gatewayBase);
  }, [gatewayBase]);

  const login = (newToken) => {
    setToken(newToken);
  };

  const signOut = () => {
    setToken('');
  };

  return (
    <AuthContext.Provider value={{ token, globalRole, gatewayBase, setGatewayBase, login, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
