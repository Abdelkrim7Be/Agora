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
 *
 * The banner itself stays a single compact line regardless of how many
 * queries failed — a growing bullet list of raw query keys read as a debug
 * console, not a production surface. Anyone who wants the technical detail
 * opens it explicitly, in a modal, instead of it always taking up page space.
 */
export function DataErrorBanner() {
  const [allFailures, setFailures] = useState([]);
  const [showDetails, setShowDetails] = useState(false);
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
    setShowDetails(false);
    queryClient.refetchQueries({ type: 'active' });
  };

  return (
    <>
      <div className="notice error data-error-banner" role="alert">
        <div>
          {dataFailures.length ? (
            <>
              <strong>Une partie de cette page n’a pas pu être chargée.</strong>
              <p>Les chiffres et les listes affichés peuvent être incomplets.</p>
            </>
          ) : (
            <>
              <strong>Action refusée.</strong>
              {actionFailure ? <p>{actionFailure.message}</p> : null}
            </>
          )}
        </div>
        <div className="data-error-actions">
          {dataFailures.length ? (
            <button type="button" className="ghost" onClick={() => setShowDetails(true)}>
              <span className="material-symbols-outlined" aria-hidden="true">info</span>
              <span>Détails{dataFailures.length > 1 ? ` (${dataFailures.length})` : ''}</span>
            </button>
          ) : null}
          <button type="button" onClick={retry}>
            <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
            <span>Réessayer</span>
          </button>
        </div>
      </div>

      {showDetails ? (
        <div className="modal">
          <div className="modal-backdrop" onClick={() => setShowDetails(false)}></div>
          <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="data-error-details-title">
            <div className="modal-header">
              <div>
                <h2 id="data-error-details-title">Détails techniques</h2>
                <p>Contactez votre administrateur si le problème persiste après un nouvel essai.</p>
              </div>
              <button className="ghost icon-button" type="button" aria-label="Fermer" onClick={() => setShowDetails(false)}>
                <span className="material-symbols-outlined" aria-hidden="true">close</span>
              </button>
            </div>
            <ul className="data-error-detail-list">
              {dataFailures.map((entry) => (
                <li key={entry.id}>
                  <code>{entry.subject}</code>
                  <p>{entry.message}</p>
                </li>
              ))}
            </ul>
            <div className="dialog-actions">
              <button className="primary" type="button" onClick={retry}>
                <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
                <span>Réessayer</span>
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
