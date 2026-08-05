import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeToFailures, clearAllFailures } from '../../api/failureLog';

const FORBIDDEN = 'Interdit pour ce rôle.';

/**
 * Shown whenever a request behind the current screen failed. Without it the
 * page would present an empty list as a confirmed fact.
 *
 * A 403 is not a failure — it is the permission model working as designed, and
 * showing it as a red alert made every workspace open on an alarm about a
 * setting the person was never meant to see. Those are filtered out here; the
 * pages that gate on a role hide their own controls already.
 */
export function DataErrorBanner() {
  const [allFailures, setFailures] = useState([]);
  const queryClient = useQueryClient();

  useEffect(() => subscribeToFailures(setFailures), []);

  const failures = allFailures.filter((entry) => entry.message !== FORBIDDEN);
  if (failures.length === 0) return null;

  const retry = () => {
    clearAllFailures();
    queryClient.refetchQueries({ type: 'active' });
  };

  return (
    <div className="notice error data-error-banner" role="alert">
      <div>
        <strong>Une partie de cette page n’a pas pu être chargée.</strong>
        <p>
          Les chiffres et les listes affichés peuvent être incomplets. Réessayez, ou contactez
          votre administrateur si le problème persiste.
        </p>
        <ul className="data-error-list">
          {failures.map((entry) => (
            <li key={entry.id}>
              <code>{entry.subject}</code> — {entry.message}
            </li>
          ))}
        </ul>
      </div>
      <button type="button" onClick={retry}>
        <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
        <span>Réessayer</span>
      </button>
    </div>
  );
}
