import { createContext, useContext, useState, useCallback } from 'react';

const StatusContext = createContext(null);

export function StatusProvider({ children }) {
  const [status, setStatusState] = useState({ message: 'Prêt.', kind: '' });

  const setStatus = useCallback((message, kind = '') => {
    setStatusState({ message: message || 'Prêt.', kind });
  }, []);

  return (
    <StatusContext.Provider value={{ ...status, setStatus }}>
      {children}
    </StatusContext.Provider>
  );
}

export const useStatus = () => useContext(StatusContext);
