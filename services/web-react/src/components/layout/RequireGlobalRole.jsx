import { useAuth } from '../../contexts/AuthContext';
import { roleAtLeast } from '../../utils/roles';
import { PageHeading } from './PageHeading';
import { Card } from '../ui/Card';

/**
 * Says a view is out of reach instead of letting it try.
 *
 * These pages read endpoints the gateway reserves for administrators. Their
 * sidebar entries are already hidden, but a direct link, a bookmark or a stale
 * tab still landed on them — and the page then fired four requests that all
 * came back 403, leaving the person with error toasts and no explanation.
 */
export function RequireGlobalRole({ minimum = 'admin', view, children }) {
  const { globalRole } = useAuth();
  if (roleAtLeast(globalRole, minimum)) return children;

  return (
    <>
      {view ? <PageHeading view={view} /> : null}
      <Card>
        <div className="card-header">
          <div>
            <h2>Réservé aux administrateurs</h2>
            <div className="meta">
              <span>Votre rôle actuel : {globalRole || 'inconnu'}</span>
            </div>
          </div>
        </div>
        <p>
          Cette page expose des réglages de la plateforme. Demandez à un administrateur
          de vous l’ouvrir, ou de faire la modification à votre place.
        </p>
      </Card>
    </>
  );
}
