import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useStatus } from '../../contexts/StatusContext';
import { useAuth } from '../../contexts/AuthContext';
import { currentUsername } from '../../utils/jwt';
import { roleLabelFr } from '../../utils/format';
import { useUsersQuery, useCreateUser, useSetUserEnabled, useInviteUser } from '../../api/queries';

export default function UsersPage() {
  const { setStatus } = useStatus();
  const { token } = useAuth();
  const query = useUsersQuery();
  const createUser = useCreateUser();
  const setUserEnabled = useSetUserEnabled();
  const inviteUser = useInviteUser();
  const [form, setForm] = useState({ username: '', email: '', password: '', role: 'viewer', department: '' });
  const [inviteLink, setInviteLink] = useState('');
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
    if (!form.username) return;
    try {
      const payload = {
        username: form.username,
        role: form.role,
        department: form.department || null,
      };
      if (form.email) payload.email = form.email;
      if (form.password) payload.password = form.password;
      const result = await createUser.mutateAsync(payload);
      setInviteLink(result.setupLink || '');
      setStatus(result.setupLink ? `Utilisateur ${form.username} créé. Lien d’invitation prêt à transmettre.` : `Utilisateur ${form.username} créé.`, 'ok');
      setForm({ username: '', email: '', password: '', role: 'viewer', department: '' });
    } catch (error) {
      setStatus(`Impossible de créer l’utilisateur : ${error.message}`, 'error');
    }
  };

  const handleInvite = async (user) => {
    try {
      const result = await inviteUser.mutateAsync(user.id);
      setInviteLink(result.setupLink || '');
      setStatus(result.setupLink ? `Invitation recréée pour ${user.username}.` : `Invitation envoyée à ${user.email || user.username}.`, 'ok');
    } catch (error) {
      setStatus(`Impossible d’envoyer l’invitation : ${error.message}`, 'error');
    }
  };

  const copyInviteLink = async () => {
    if (!inviteLink) return;
    await navigator.clipboard?.writeText(inviteLink);
    setStatus('Lien d’invitation copié.', 'ok');
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
      <Card className="users-card">
        <div className="card-header">
          <div>
            <h2>Utilisateurs et invitations</h2>
            <div className="meta"><span>Comptes, rôles et accès initial</span></div>
          </div>
          <button type="button" onClick={async () => { await query.refetch(); setStatus('Utilisateurs chargés.', 'ok'); }}>
            <span className="material-symbols-outlined" aria-hidden="true">sync</span>
            <span>Actualiser</span>
          </button>
        </div>
        <div className="notice">
          Invitez un collaborateur sans partager de mot de passe temporaire. Si aucun relais SMTP n’est configuré, Agora AI affiche un lien sécurisé à transmettre manuellement.
        </div>
        {inviteLink ? (
          <div className="notice invite-link-notice">
            <strong>Lien d’invitation à transmettre</strong>
            <div className="invite-copy-row">
              <input readOnly value={inviteLink} aria-label="Lien d’invitation" />
              <button type="button" onClick={copyInviteLink}>
                <span className="material-symbols-outlined" aria-hidden="true">content_copy</span>
                <span>Copier</span>
              </button>
            </div>
          </div>
        ) : null}
        <div className="table-wrap users-table">
          <table className="data-table">
            <thead><tr><th>Utilisateur</th><th>E-mail</th><th>Rôle</th><th>Département</th><th>Statut</th><th></th></tr></thead>
            <tbody>
              {users.map((user) => {
                const selfDisable = user.enabled && user.username === username;
                const label = user.enabled ? 'Désactiver' : 'Activer';
                return (
                  <tr key={user.id}>
                    <td>{user.username}</td>
                    <td>{user.email || '—'}</td>
                    <td><span className="status-pill">{roleLabelFr(user.role)}</span></td>
                    <td>{user.department || '—'}</td>
                    <td>{user.enabled ? 'Actif' : 'Désactivé'}</td>
                    <td>
                      <button type="button" onClick={() => handleInvite(user)}>
                        Inviter
                      </button>
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
        <form className="user-create-form" style={{ marginTop: '1rem' }} onSubmit={handleCreate}>
          <input
            placeholder="Nom d'utilisateur"
            required
            style={{ flex: 1 }}
            value={form.username}
            onChange={(event) => setForm({ ...form, username: event.target.value })}
          />
          <input
            type="email"
            placeholder="E-mail d'invitation"
            style={{ flex: 1 }}
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
          />
          <input
            type="password"
            placeholder="Mot de passe temporaire (optionnel)"
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
