import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useStatus } from '../../contexts/StatusContext';
import { useAuth } from '../../contexts/AuthContext';
import { currentUsername } from '../../utils/jwt';
import { roleLabelFr } from '../../utils/format';
import { useUsersQuery, useCreateUser, useSetUserEnabled } from '../../api/queries';

export default function UsersPage() {
  const { setStatus } = useStatus();
  const { token } = useAuth();
  const query = useUsersQuery();
  const createUser = useCreateUser();
  const setUserEnabled = useSetUserEnabled();
  const [form, setForm] = useState({ username: '', password: '', role: 'viewer', department: '' });
  const username = currentUsername(token);
  const announcedInitialLoad = useRef(false);

  // Only toast on the very first load — a mutation's own invalidateQueries refetch
  // shouldn't clobber that mutation's more specific success message with this one.
  useEffect(() => {
    if (query.data && !announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Utilisateurs chargés.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger les utilisateurs : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const users = query.data || [];

  const handleCreate = async (event) => {
    event.preventDefault();
    if (!form.username || !form.password) return;
    try {
      await createUser.mutateAsync({
        username: form.username,
        password: form.password,
        role: form.role,
        department: form.department || null,
      });
      setForm({ username: '', password: '', role: 'viewer', department: '' });
      setStatus(`Utilisateur ${form.username} créé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de créer l’utilisateur : ${error.message}`, 'error');
    }
  };

  const handleToggle = async (user) => {
    const enabling = !user.enabled;
    if (!enabling && user.username === username) {
      setStatus('Vous ne pouvez pas désactiver votre propre compte actif.', 'error');
      return;
    }
    try {
      await setUserEnabled.mutateAsync({ id: user.id, enabled: enabling });
      setStatus(enabling ? 'Utilisateur activé.' : 'Utilisateur désactivé.', 'ok');
    } catch (error) {
      setStatus(`Impossible de mettre à jour l’utilisateur : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="users" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Utilisateurs</h2>
            <div className="meta"><span>Annuaire du personnel — admin uniquement</span></div>
          </div>
          <button type="button" onClick={async () => { await query.refetch(); setStatus('Utilisateurs chargés.', 'ok'); }}>
            <span className="material-symbols-outlined" aria-hidden="true">sync</span>
            <span>Actualiser</span>
          </button>
        </div>
        <div className="notice">
          Rôles : <strong>administrateur</strong> (utilisateurs, secrets, boîtes mail, config système), <strong>propriétaire</strong> (workflows, personas, toutes les approbations), <strong>validateur</strong> (approbations de son département), <strong>lecteur</strong> (lecture seule). Le département reprend le vocabulaire de l'Annuaire des rôles (RH, Finance, ...).
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Utilisateur</th><th>Rôle</th><th>Département</th><th>Statut</th><th></th></tr></thead>
            <tbody>
              {users.map((user) => {
                const selfDisable = user.enabled && user.username === username;
                const label = user.enabled ? 'Désactiver' : 'Activer';
                return (
                  <tr key={user.id}>
                    <td>{user.username}</td>
                    <td><span className="status-pill">{roleLabelFr(user.role)}</span></td>
                    <td>{user.department || '—'}</td>
                    <td>{user.enabled ? 'Actif' : 'Désactivé'}</td>
                    <td>
                      <button
                        type="button"
                        disabled={selfDisable}
                        title={selfDisable ? 'Vous ne pouvez pas désactiver votre propre compte actif' : undefined}
                        aria-label={`${label} ${user.username || 'utilisateur'}`}
                        onClick={() => handleToggle(user)}
                      >
                        {label}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!users.length && <div className="notice">Aucun compte utilisateur pour le moment.</div>}
        <form className="toolbar" style={{ marginTop: '1rem' }} onSubmit={handleCreate}>
          <input
            placeholder="Nom d'utilisateur"
            required
            style={{ flex: 1 }}
            value={form.username}
            onChange={(event) => setForm({ ...form, username: event.target.value })}
          />
          <input
            type="password"
            placeholder="Mot de passe temporaire"
            required
            style={{ flex: 1 }}
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
          />
          <select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
            <option value="viewer">lecteur</option>
            <option value="approver">validateur</option>
            <option value="owner">propriétaire</option>
            <option value="admin">administrateur</option>
          </select>
          <input
            placeholder="Département (optionnel)"
            style={{ flex: 1 }}
            value={form.department}
            onChange={(event) => setForm({ ...form, department: event.target.value })}
          />
          <button type="submit" className="primary">
            <span className="material-symbols-outlined" aria-hidden="true">person_add</span>
            <span>Créer</span>
          </button>
        </form>
      </Card>
    </>
  );
}
