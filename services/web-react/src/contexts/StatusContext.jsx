import { createContext, useContext, useState, useCallback } from 'react';
import { toast } from 'sonner';

const StatusContext = createContext(null);

// Errors need reading, and often re-reading — Sonner's own default duration
// is tuned for a quick confirmation, not a message someone might copy.
const ERROR_DURATION_MS = 9000;

export function StatusProvider({ children }) {
  const [status, setStatusState] = useState({ message: 'Prêt.', kind: '' });

  const setStatus = useCallback((message, kind = '') => {
    setStatusState({ message: message || 'Prêt.', kind });
    if (!message) return;
    if (kind === 'error') toast.error(message, { duration: ERROR_DURATION_MS });
    else if (kind === 'warn') toast.warning(message);
    else if (kind === 'ok') toast.success(message);
    else toast(message);
  }, []);

  return (
    <StatusContext.Provider value={{ ...status, setStatus }}>
      {children}
    </StatusContext.Provider>
  );
}

export const useStatus = () => useContext(StatusContext);
