import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeToFailures, clearAllFailures, ACTION_FAILURE_ID } from '../../api/failureLog';

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

  // A refused action isn't a page that failed to load — nothing on screen is
  // stale or incomplete, one click was declined. Saying "les chiffres et les
  // listes peuvent être incomplets" about that is simply wrong, so it gets its
  // own line instead of borrowing the read-failure copy.
  const actionFailure = failures.find((entry) => entry.id === ACTION_FAILURE_ID);
  const dataFailures = failures.filter((entry) => entry.id !== ACTION_FAILURE_ID);

  const retry = () => {
    clearAllFailures();
    queryClient.refetchQueries({ type: 'active' });
  };

  return (
    <div className="notice error data-error-banner" role="alert">
      <div>
        {dataFailures.length ? (
          <>
            <strong>Une partie de cette page n’a pas pu être chargée.</strong>
            <p>
              Les chiffres et les listes affichés peuvent être incomplets. Réessayez, ou contactez
              votre administrateur si le problème persiste.
            </p>
          </>
        ) : (
          <strong>Action refusée.</strong>
        )}
        <ul className="data-error-list">
          {dataFailures.map((entry) => (
            <li key={entry.id}>
              <code>{entry.subject}</code>
              {/* The raw backend message (stack traces, "invalid_scope: Bad
                  Request", connection internals) doesn't belong in a banner
                  meant to say "something needs attention" — it read as if
                  every ordinary failure (an expired OAuth token, say) were a
                  security event. Kept, not discarded: a person troubleshooting
                  can still expand it. */}
              <details className="data-error-detail">
                <summary>Détails techniques</summary>
                <code>{entry.message}</code>
              </details>
            </li>
          ))}
          {actionFailure ? (
            <li key={actionFailure.id}>
              <code>{actionFailure.message}</code>
            </li>
          ) : null}
        </ul>
      </div>
      <button type="button" onClick={retry}>
        <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
        <span>Réessayer</span>
      </button>
    </div>
  );
}
