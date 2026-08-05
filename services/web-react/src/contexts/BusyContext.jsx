import { createContext, useCallback, useContext, useRef, useState } from 'react';

const BusyContext = createContext(null);

/**
 * One place to say "the platform is working on it".
 *
 * Several actions here are slow by nature — a local model redrafting an email,
 * a Gmail round trip, a campaign send. Without a visible state they looked like
 * dead buttons: the click registered, nothing moved for ten seconds, and the
 * only feedback was a small toast in the page header that was easy to miss. The
 * overlay also blocks a second click on the same action while the first runs.
 */
export function BusyProvider({ children }) {
  const [label, setLabel] = useState('');
  // Nested/parallel calls share one overlay; only the last one out clears it.
  const depth = useRef(0);

  const runBusy = useCallback(async (busyLabel, task) => {
    depth.current += 1;
    setLabel(busyLabel);
    try {
      return await task();
    } finally {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setLabel('');
    }
  }, []);

  return (
    <BusyContext.Provider value={{ runBusy, busy: Boolean(label) }}>
      {children}
      {label ? (
        <div className="busy-overlay" role="alertdialog" aria-live="assertive" aria-busy="true" aria-label={label}>
          <div className="busy-panel">
            <span className="busy-spinner" aria-hidden="true" />
            <strong>{label}</strong>
            <span>Traitement en cours, merci de patienter.</span>
          </div>
        </div>
      ) : null}
    </BusyContext.Provider>
  );
}

export const useBusy = () => useContext(BusyContext);
