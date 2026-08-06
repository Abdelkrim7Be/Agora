import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useStatus } from '../../contexts/StatusContext';
import { useAuth } from '../../contexts/AuthContext';
import { useUsersQuery, useGrantsQuery, useAddGrant, useRemoveGrant } from '../../api/queries';
import { formatDateTimeFr, roleLabelFr } from '../../utils/format';

export default function PermissionsPage() {
  const { setStatus } = useStatus();
  const { globalRole } = useAuth();
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState('approver');
  const [expiresAt, setExpiresAt] = useState('');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const canListUsers = globalRole === 'admin';
  const usersQuery = useUsersQuery(canListUsers);
  const grantsQuery = useGrantsQuery();
  const addGrant = useAddGrant();
  const removeGrant = useRemoveGrant();

  const grants = grantsQuery.data || [];
  const activeUsers = (usersQuery.data || []).filter((user) => user.enabled !== false);

  useEffect(() => {
    if (!grantsQuery.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Permissions chargées.', 'ok');
    }
  }, [grantsQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!grantsQuery.error || announcedError.current === grantsQuery.error.message) return;
    announcedError.current = grantsQuery.error.message;
    setStatus(`Impossible de charger les délégations : ${grantsQuery.error.message}`, 'error');
  }, [grantsQuery.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleReload = () => { grantsQuery.refetch(); usersQuery.refetch(); };

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!userId) {
      setStatus("Choisissez un utilisateur avant d'ajouter une délégation.", 'error');
      return;
    }
    try {
      await addGrant.mutateAsync({
        userId,
        role,
        expiresAt: expiresAt ? new Date(expiresAt).toISOString() : '',
      });
      setStatus(`Rôle ${role} accordé à ${userId}.`, 'ok');
      setUserId('');
      setExpiresAt('');
    } catch (error) {
      setStatus(`Impossible d'ajouter la délégation : ${error.message}`, 'error');
    }
  };

  const handleRemove = async (targetUserId) => {
    try {
      await removeGrant.mutateAsync(targetUserId);
      setStatus(`Délégation révoquée pour ${targetUserId}.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de révoquer la délégation : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="permissions" />
      <Card>
        <div className="card-header">
          <div><h2>Permissions</h2><div className="meta"><span>Délégations de rôle par instance</span></div></div>
          <button type="button" onClick={handleReload}>
            <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Actualiser</span>
          </button>
        </div>
        <div className="notice">
          Une délégation donne à un utilisateur un rôle sur cette instance uniquement : <strong>propriétaire</strong> (configurer + approuver), <strong>validateur</strong> (relire/envoyer les brouillons), <strong>lecteur</strong> (consulter). Les délégations lecteur expirent automatiquement si aucune date n’est fournie.
        </div>
        <div className="grants-panel">
          {grants.length ? (
            <div className="rule-list grants-list">
              {grants.map((g) => (
                <div className="directory-row" key={g.user_id}>
                  <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">person</span></div>
                  <div className="directory-main">
                    <strong>{g.user_id}</strong>
                    <div className="mini-chip-row">
                      <span className="status-pill">{roleLabelFr(g.role)}</span>
                      <span className="mini-chip">accordé par {g.granted_by || '—'}</span>
                      <span className="mini-chip">{g.granted_at ? formatDateTimeFr(g.granted_at) : '—'}</span>
                      {g.expires_at ? <span className="mini-chip">expire {formatDateTimeFr(g.expires_at)}</span> : null}
                    </div>
                  </div>
                  <div className="directory-actions">
                    <button className="danger" type="button" onClick={() => handleRemove(g.user_id)}>
                      <span className="material-symbols-outlined" aria-hidden="true">remove_moderator</span><span>Révoquer</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty grants-empty">Aucune délégation explicite pour cette instance. Les utilisateurs y accèdent via leur rôle JWT global uniquement.</div>
          )}
          <form className="grants-add-row" onSubmit={handleAdd}>
            {canListUsers ? (
              <select className="grants-user-select" aria-label="Utilisateur à autoriser" value={userId} onChange={(e) => setUserId(e.target.value)}>
                <option value="">Choisir un utilisateur</option>
                {activeUsers.map((user) => {
                  const details = [user.role, user.department].filter(Boolean).join(' / ');
                  return <option key={user.username} value={user.username}>{user.username}{details ? ` (${details})` : ''}</option>;
                })}
              </select>
            ) : (
              <input
                className="grants-user-select"
                aria-label="Utilisateur à autoriser"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="identifiant utilisateur"
              />
            )}
            <select className="grants-role-select" aria-label="Rôle sur l'instance" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="approver">validateur</option>
              <option value="viewer">lecteur</option>
              <option value="owner">propriétaire</option>
            </select>
            {role === 'viewer' ? (
              <input
                className="grants-expiry-input"
                type="datetime-local"
                aria-label="Expiration de la délégation lecteur"
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
              />
            ) : null}
            <div className="directory-actions">
              <button className="primary" type="submit" disabled={!userId}>
                <span className="material-symbols-outlined" aria-hidden="true">add</span><span>Accorder</span>
              </button>
            </div>
          </form>
        </div>
      </Card>
    </>
  );
}
