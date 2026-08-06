import { useEffect, useRef, useState } from 'react';
import { TablePager } from '../../components/ui/TablePager';
import { usePagination } from '../../hooks/usePagination';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useStatus } from '../../contexts/StatusContext';
import { useAuth } from '../../contexts/AuthContext';
import { useDialog } from '../../contexts/DialogContext';
import { currentUsername } from '../../utils/jwt';
import { agentTypeLabel, formatDateTimeFr, roleLabelFr } from '../../utils/format';
import {
  useUsersQuery,
  useCreateUser,
  useSetUserEnabled,
  useInviteUser,
  useUpdateUser,
  useSetUserPassword,
  useAgentInstancesQuery,
  useAgentTypesQuery,
  useInstanceGrantsQuery,
  useAddInstanceGrant,
  useRemoveInstanceGrant,
} from '../../api/queries';

function onboardingState(user) {
  if (user.enabled) return { label: 'Configuré', tone: 'ok' };
  if (user.invitationExpired) return { label: 'Invitation expirée', tone: 'error' };
  if (user.pendingInvitation) return { label: 'Invitation en attente', tone: 'warn' };
  return { label: 'À inviter', tone: 'warn' };
}

export default function UsersPage() {
  const { setStatus } = useStatus();
  const { token } = useAuth();
  const { promptDialog } = useDialog();
  const query = useUsersQuery();
  const createUser = useCreateUser();
  const setUserEnabled = useSetUserEnabled();
  const inviteUser = useInviteUser();
  const updateUser = useUpdateUser();
  const setUserPassword = useSetUserPassword();
  const instancesQuery = useAgentInstancesQuery();
  const typesQuery = useAgentTypesQuery();
  const grantsQuery = useInstanceGrantsQuery(instancesQuery.data || [], true);
  const addGrant = useAddInstanceGrant();
  const removeGrant = useRemoveInstanceGrant();
  const [form, setForm] = useState({ username: '', email: '', password: '', role: 'viewer', department: '' });
  const [editForms, setEditForms] = useState({});
  const [accessForm, setAccessForm] = useState({ userId: '', instanceId: '', role: 'viewer' });
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
  const userPager = usePagination(users);
  const instances = instancesQuery.data || [];
  const agentTypes = typesQuery.data || [];
  const grants = grantsQuery.data || [];

  const instancesForUser = (user) => instances.filter((instance) => (
    instance.created_by === user.username || grants.some((grant) => (
      grant.user_id === user.username && grant.agent_instance_id === instance.id
    ))
  ));

  const grantFor = (user, instance) => grants.find((grant) => (
    grant.user_id === user.username && grant.agent_instance_id === instance.id
  ));

  const editFormFor = (user) => editForms[user.id] || {
    role: user.role || 'viewer',
    department: user.department || '',
    email: user.email || '',
  };

  const setEditForm = (user, patch) => {
    setEditForms((current) => ({ ...current, [user.id]: { ...editFormFor(user), ...patch } }));
  };

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

  // An administrator resets a password without knowing the old one — that is the
  // point of a reset. It is deliberately a separate action from the role/e-mail
  // save so it can never ride along with an unrelated edit.
  const handleSetPassword = async (user) => {
    const password = await promptDialog({
      title: `Définir le mot de passe de ${user.username}`,
      message: 'Au moins 12 caractères. Communiquez-le par un canal sûr — il ne sera plus affiché ensuite.',
      placeholder: 'Nouveau mot de passe',
      confirmLabel: 'Définir',
      required: true,
    });
    if (!password) return;
    if (password.length < 12) {
      setStatus('Le mot de passe doit faire au moins 12 caractères.', 'error');
      return;
    }
    try {
      await setUserPassword.mutateAsync({ id: user.id, password });
      setStatus(`Mot de passe de ${user.username} défini.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de définir le mot de passe : ${error.message}`, 'error');
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

  const handleUpdateUser = async (user) => {
    const edit = editFormFor(user);
    try {
      await updateUser.mutateAsync({
        id: user.id,
        role: edit.role,
        department: edit.department,
        email: edit.email,
      });
      setStatus(`Accès de ${user.username} mis à jour.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de mettre à jour ${user.username} : ${error.message}`, 'error');
    }
  };

  const handleGrant = async (event) => {
    event.preventDefault();
    if (!accessForm.userId || !accessForm.instanceId) return;
    try {
      await addGrant.mutateAsync(accessForm);
      setStatus('Accès à l’instance mis à jour.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’ajouter l’accès : ${error.message}`, 'error');
    }
  };

  const handleRemoveGrant = async (instanceId, userId) => {
    try {
      await removeGrant.mutateAsync({ instanceId, userId });
      setStatus('Accès retiré.', 'ok');
    } catch (error) {
      setStatus(`Impossible de retirer l’accès : ${error.message}`, 'error');
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
            <thead><tr><th>Utilisateur</th><th>E-mail</th><th>Rôle plateforme</th><th>Département</th><th>Configuration</th><th></th></tr></thead>
            <tbody>
              {userPager.visible.map((user) => {
                const selfDisable = user.enabled && user.username === username;
                const label = user.enabled ? 'Désactiver' : 'Activer';
                const onboarding = onboardingState(user);
                const edit = editFormFor(user);
                return (
                  <tr key={user.id}>
                    <td>{user.username}</td>
                    <td>
                      <input
                        type="email"
                        value={edit.email}
                        placeholder="aucune adresse"
                        aria-label={`Adresse e-mail de ${user.username}`}
                        onChange={(event) => setEditForm(user, { email: event.target.value })}
                      />
                    </td>
                    <td>
                      <select value={edit.role} onChange={(event) => setEditForm(user, { role: event.target.value })}>
                        <option value="viewer">lecteur</option>
                        <option value="approver">validateur</option>
                        <option value="owner">propriétaire</option>
                        <option value="admin">administrateur</option>
                      </select>
                    </td>
                    <td>
                      <input
                        value={edit.department}
                        placeholder="Département"
                        onChange={(event) => setEditForm(user, { department: event.target.value })}
                      />
                    </td>
                    <td>
                      <span className={`status-pill ${onboarding.tone}`.trim()}>{onboarding.label}</span>
                      {user.invitationExpiresAt ? <small className="muted">Expire {formatDateTimeFr(user.invitationExpiresAt)}</small> : null}
                    </td>
                    <td>
                      <button type="button" onClick={() => handleUpdateUser(user)}>
                        Enregistrer
                      </button>
                      <button type="button" onClick={() => handleInvite(user)}>
                        Inviter
                      </button>
                      <button type="button" onClick={() => handleSetPassword(user)}>
                        Mot de passe
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
        {users.length ? (
          <TablePager
            page={userPager.page}
            pageCount={userPager.pageCount}
            total={userPager.total}
            size={userPager.size}
            onPage={userPager.setPage}
            onSize={userPager.setSize}
            unit="comptes"
          />
        ) : <div className="notice">Aucun compte utilisateur pour le moment.</div>}
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
      <Card className="users-card user-instances-card">
        <div className="card-header">
          <div>
            <h2>Gestion des accès aux instances</h2>
            <div className="meta"><span>Attribuez un rôle par agent : lecture, validation ou propriété</span></div>
          </div>
        </div>
        <form className="access-grant-form" onSubmit={handleGrant}>
          <select
            value={accessForm.userId}
            onChange={(event) => setAccessForm({ ...accessForm, userId: event.target.value })}
            aria-label="Utilisateur"
            required
          >
            <option value="">Utilisateur</option>
            {users.map((user) => <option key={user.id} value={user.username}>{user.username}</option>)}
          </select>
          <select
            value={accessForm.instanceId}
            onChange={(event) => setAccessForm({ ...accessForm, instanceId: event.target.value })}
            aria-label="Instance"
            required
          >
            <option value="">Instance d’agent</option>
            {instances.map((instance) => (
              <option key={instance.id} value={instance.id}>{instance.display_name || instance.id}</option>
            ))}
          </select>
          <select
            value={accessForm.role}
            onChange={(event) => setAccessForm({ ...accessForm, role: event.target.value })}
            aria-label="Rôle sur l’instance"
          >
            <option value="viewer">lecteur</option>
            <option value="approver">validateur</option>
            <option value="owner">propriétaire</option>
          </select>
          <button className="primary" type="submit" disabled={addGrant.isPending}>
            <span className="material-symbols-outlined" aria-hidden="true">key</span>
            <span>Donner l’accès</span>
          </button>
        </form>
      </Card>
      <Card className="users-card user-instances-card">
        <div className="card-header">
          <div>
            <h2>Utilisateurs et instances</h2>
            <div className="meta"><span>Lecture rapide des agents rattachés à chaque compte</span></div>
          </div>
        </div>
        <div className="user-instance-list">
          {users.map((user) => {
            const userInstances = instancesForUser(user);
            return (
              <div className="user-instance-row" key={user.id}>
                <div>
                  <strong>{user.username}</strong>
                  <span>{roleLabelFr(user.role)} · {user.department || 'sans département'}</span>
                </div>
                <div className="user-instance-pills">
                  {userInstances.length ? userInstances.map((instance) => (
                    <span className="mini-chip grant-chip" key={instance.id}>
                      {instance.display_name || instance.id} · {agentTypeLabel(instance.agent_type, agentTypes)}
                      {instance.created_by === user.username ? ' · créateur' : ` · ${roleLabelFr(grantFor(user, instance)?.role)}`}
                      {instance.created_by !== user.username ? (
                        <button type="button" aria-label={`Retirer ${instance.display_name || instance.id} à ${user.username}`} onClick={() => handleRemoveGrant(instance.id, user.username)}>
                          <span className="material-symbols-outlined" aria-hidden="true">close</span>
                        </button>
                      ) : null}
                    </span>
                  )) : <span className="muted">Aucune instance rattachée</span>}
                </div>
              </div>
            );
          })}
        </div>
        {!users.length && <div className="notice">Aucun compte utilisateur pour le moment.</div>}
      </Card>
    </>
  );
}
