import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeToFailures, clearAllFailures } from '../../api/failureLog';

/**
 * Shown whenever a request behind the current screen failed. Without it the
 * page would present an empty list as a confirmed fact.
 */
export function DataErrorBanner() {
  const [failures, setFailures] = useState([]);
  const queryClient = useQueryClient();

  useEffect(() => subscribeToFailures(setFailures), []);

  if (failures.length === 0) return null;

  const retry = () => {
    clearAllFailures();
    queryClient.refetchQueries({ type: 'active' });
  };

  const forbidden = failures.every((entry) => entry.message === 'Interdit pour ce rôle.');

  return (
    <div className="notice error data-error-banner" role="alert">
      <div>
        <strong>
          {forbidden
            ? 'Cette page contient des informations réservées à un autre rôle.'
            : 'Une partie de cette page n’a pas pu être chargée.'}
        </strong>
        <p>
          {forbidden
            ? 'Ce qui s’affiche est donc incomplet. Demandez un accès à votre administrateur pour voir le reste.'
            : 'Les chiffres et les listes affichés peuvent être incomplets. Réessayez, ou contactez votre administrateur si le problème persiste.'}
        </p>
        <ul className="data-error-list">
          {failures.map((entry) => (
            <li key={entry.id}>
              <code>{entry.subject}</code> — {entry.message}
            </li>
          ))}
        </ul>
      </div>
      {forbidden ? null : (
        <button type="button" onClick={retry}>
          <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
          <span>Réessayer</span>
        </button>
      )}
    </div>
  );
}
