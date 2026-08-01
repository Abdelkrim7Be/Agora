import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useStatus } from '../../contexts/StatusContext';
import { useUsersQuery, useGrantsQuery, useAddGrant, useRemoveGrant } from '../../api/queries';
import { formatDateTimeFr } from '../../utils/format';

export default function PermissionsPage() {
  const { setStatus } = useStatus();
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState('approver');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const usersQuery = useUsersQuery();
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
      await addGrant.mutateAsync({ userId, role });
      setStatus(`Rôle ${role} accordé à ${userId}.`, 'ok');
      setUserId('');
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
          Une délégation donne à un utilisateur un rôle sur cette instance uniquement : <strong>owner</strong> (configurer + approuver), <strong>approver</strong> (relire/envoyer les brouillons), <strong>viewer</strong> (consulter). Le rôle JWT global s'applique à toute la plateforme ; les délégations l'étendent pour des espaces précis.
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
                      <span className="status-pill">{g.role}</span>
                      <span className="mini-chip">accordé par {g.granted_by || '—'}</span>
                      <span className="mini-chip">{g.granted_at ? formatDateTimeFr(g.granted_at) : '—'}</span>
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
            <select className="grants-user-select" aria-label="Utilisateur à autoriser" value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">Choisir un utilisateur</option>
              {activeUsers.map((user) => {
                const details = [user.role, user.department].filter(Boolean).join(' / ');
                return <option key={user.username} value={user.username}>{user.username}{details ? ` (${details})` : ''}</option>;
              })}
            </select>
            <select className="grants-role-select" aria-label="Rôle sur l'instance" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="approver">approver</option>
              <option value="viewer">viewer</option>
              <option value="owner">owner</option>
            </select>
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
