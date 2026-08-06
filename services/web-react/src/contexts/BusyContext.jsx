import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useIsMutating } from '@tanstack/react-query';

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
// How long an action may run before it has to admit it is running. Below this a
// spinner is worse than nothing: it flashes up and vanishes, which reads as a
// glitch rather than as progress.
const VISIBLE_AFTER_MS = 700;

export function BusyProvider({ children }) {
  const [label, setLabel] = useState('');
  const [autoLabel, setAutoLabel] = useState('');
  // Nested/parallel calls share one overlay; only the last one out clears it.
  const depth = useRef(0);

  // Safety net for every write that was never wrapped by hand. Wrapping ninety
  // call sites one at a time guarantees the next one added is forgotten, so the
  // rule lives here instead: any mutation still in flight after the threshold
  // gets an overlay whether or not anyone remembered to ask for one. Reads are
  // deliberately excluded — blocking the screen to fetch something is wrong;
  // that belongs in the view as a skeleton.
  const mutating = useIsMutating();
  useEffect(() => {
    if (!mutating) {
      setAutoLabel('');
      return undefined;
    }
    const timer = setTimeout(() => setAutoLabel('Traitement en cours'), VISIBLE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [mutating]);

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

  // An explicit label always wins: "Envoi en cours" tells you more than "Traitement".
  const shown = label || autoLabel;

  return (
    <BusyContext.Provider value={{ runBusy, busy: Boolean(shown) }}>
      {children}
      {shown ? (
        <div className="busy-overlay" role="alertdialog" aria-live="assertive" aria-busy="true" aria-label={shown}>
          <div className="busy-panel">
            <span className="busy-spinner" aria-hidden="true" />
            <strong>{shown}</strong>
            <span>Traitement en cours, merci de patienter.</span>
          </div>
        </div>
      ) : null}
    </BusyContext.Provider>
  );
}

export const useBusy = () => useContext(BusyContext);
