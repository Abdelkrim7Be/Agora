import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge } from '../../components/ui/Badge';
import { EmptyState } from '../../components/ui/EmptyState';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance, isInstanceActive } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useApi } from '../../api/useApi';
import { agentTypeLabel, instanceIdentity, instanceSummaryFields } from '../../utils/format';
import {
  useAgentInstancesQuery,
  useAgentTypesQuery,
  useRenameInstance,
  useDeleteInstance,
  useCreateInstance,
  useUsersQuery,
} from '../../api/queries';

const EMPTY_CREATE_FORM = { agentType: '', displayName: '', assignedTo: '' };

function instanceStatus(instance) {
  return String(instance?.status || 'active').toLowerCase();
}

export default function InstancesPage() {
  const { globalRole } = useAuth();
  const { instances, setInstances, setInstanceId } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog, promptDialog } = useDialog();
  const navigate = useNavigate();
  const { api } = useApi();
  const query = useAgentInstancesQuery();
  const typesQuery = useAgentTypesQuery();
  const usersQuery = useUsersQuery(globalRole === 'admin');
  const renameInstance = useRenameInstance();
  const deleteInstance = useDeleteInstance();
  const createInstance = useCreateInstance();
  const canManage = globalRole === 'admin';
  const announcedInitialLoad = useRef(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(EMPTY_CREATE_FORM);
  const [createError, setCreateError] = useState('');

  const agentTypes = typesQuery.data || [];

  const openCreate = () => {
    setCreateForm({ ...EMPTY_CREATE_FORM, agentType: agentTypes[0]?.id || '' });
    setCreateError('');
    setCreateOpen(true);
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    const displayName = createForm.displayName.trim();
    if (!createForm.agentType || !displayName) {
      setCreateError('Type d’agent et nom sont requis.');
      return;
    }
    const oauthPopup = window.open('', 'agora-gmail-connect', 'popup=yes,width=520,height=720');
    if (!oauthPopup) {
      setCreateError('Popup bloquée. Autorisez les popups pour ce site puis recréez l’instance.');
      return;
    }
    oauthPopup.document.write('<!doctype html><title>Connexion Gmail</title><body style="font-family:system-ui,sans-serif;padding:24px;background:#0b1326;color:#dae2fd">Création de l’instance puis ouverture du consentement Google...</body>');
    try {
      const created = await createInstance.mutateAsync({
        agentType: createForm.agentType,
        displayName,
        mailboxIdentity: '',
        assignedTo: createForm.assignedTo,
      });
      setCreateOpen(false);
      if (created?.id) {
        setInstanceId(created.id);
        // Keep the app on setup while Google OAuth happens in a separate window.
        // The setup screen polls Gmail status and starts onboarding when the
        // callback stores the token.
        try {
          const result = await api(
            `/api/agent/agent-instances/${encodeURIComponent(created.id)}/connect/gmail/start`
          );
          navigate(`/instance/${created.id}/setup`);
          oauthPopup.location.href = result.authorization_url;
          oauthPopup.focus();
          setStatus('Connectez Gmail dans la fenêtre Google. La configuration démarrera automatiquement.', 'ok');
        } catch (gmailError) {
          oauthPopup.close();
          setStatus(`Instance « ${displayName} » créée, mais connexion Gmail impossible : ${gmailError.message}`, 'error');
          navigate(`/instance/${created.id}`);
        }
      }
    } catch (error) {
      oauthPopup.close();
      setCreateError(error.message);
    }
  };

  useEffect(() => {
    if (!query.data) return;
    setInstances(query.data);
    // Only toast on the very first load — a mutation's own invalidateQueries
    // refetch shouldn't clobber that mutation's more specific success message.
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Instances d’agents chargées.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger les instances : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleOpen = (instance) => {
    setInstanceId(instance.id);
    const setupStatus = instance.summary?.setup_status;
    const needsSetup = setupStatus !== 'ready';
    navigate(needsSetup ? `/instance/${instance.id}/setup` : `/instance/${instance.id}`);
  };

  const handleRename = async (instance) => {
    const currentName = instance.display_name || instance.id;
    const nextName = await promptDialog({
      title: 'Renommer l’instance',
      message: 'Choisissez le nom affiché dans la plateforme.',
      placeholder: 'Nom de l’instance',
      confirmLabel: 'Renommer',
      defaultValue: currentName,
      required: true,
    });
    if (!nextName || nextName === currentName) return;
    try {
      await renameInstance.mutateAsync({ id: instance.id, displayName: nextName });
      setStatus(`Instance renommée en « ${nextName} ».`, 'ok');
    } catch (error) {
      setStatus(`Impossible de renommer l’instance : ${error.message}`, 'error');
    }
  };

  const handleDelete = async (instance) => {
    const label = instance.display_name || instance.id;
    const confirmed = await confirmDialog({
      title: 'Supprimer l’instance',
      message: `${label} sera retirée de la plateforme. Les données propres à l’agent restent gérées par le service de l’agent.`,
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteInstance.mutateAsync(instance.id);
      setStatus(`Instance « ${label} » supprimée.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer l’instance : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="instances" />
      <div className="toolbar">
        <button className="primary" type="button" disabled={!canManage} title={canManage ? 'Créer une instance' : 'Rôle admin requis'} onClick={openCreate}>
          <span className="material-symbols-outlined" aria-hidden="true">add</span>
          <span>Ajouter une instance</span>
        </button>
        <button type="button" onClick={async () => { await query.refetch(); setStatus('Instances d’agents chargées.', 'ok'); }}>
          <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
          <span>Actualiser</span>
        </button>
      </div>
      <div className="instance-grid">
        {!instances.length ? (
          <EmptyState message="Aucune instance visible." />
        ) : (
          instances.map((instance) => {
            const summary = instance.summary || {};
            const status = instanceStatus(instance);
            const active = isInstanceActive(instance);
            const health = summary.service_health || status || 'unknown';
            const typeLabel = agentTypeLabel(instance.agent_type, typesQuery.data || []);
            const identity = instanceIdentity(instance);
            const openTitle = active ? 'Ouvrir l’espace de travail' : 'Activez cette instance avant de l’ouvrir';
            const renameTitle = canManage ? 'Renommer l’instance' : 'Rôle admin requis';
            const deleteTitle = canManage ? 'Supprimer l’instance' : 'Rôle admin requis';
            return (
              <Card key={instance.id} className={`instance-card ${active ? '' : 'inactive'}`}>
                <div className="card-header">
                  <div className="instance-heading">
                    <h2>{instance.display_name || instance.id}</h2>
                    <div className="instance-meta">
                      <div className="instance-primary-meta">
                        <span>{typeLabel}</span>
                        <span className="meta-separator" aria-hidden="true">·</span>
                        <span className="instance-identity" title={identity}>{identity}</span>
                      </div>
                      <div className="instance-status-row">
                        <StatusBadge status={status} />
                        <StatusBadge status={health} />
                      </div>
                    </div>
                  </div>
                  <div className="card-actions instance-actions">
                    <button
                      className="primary icon-button"
                      type="button"
                      disabled={!active}
                      title={openTitle}
                      aria-label={openTitle}
                      onClick={() => handleOpen(instance)}
                    >
                      <span className="material-symbols-outlined" aria-hidden="true">open_in_new</span>
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      disabled={!canManage}
                      title={renameTitle}
                      aria-label={renameTitle}
                      onClick={() => handleRename(instance)}
                    >
                      <span className="material-symbols-outlined" aria-hidden="true">edit</span>
                    </button>
                    <button
                      className="danger icon-button"
                      type="button"
                      disabled={!canManage}
                      title={deleteTitle}
                      aria-label={deleteTitle}
                      onClick={() => handleDelete(instance)}
                    >
                      <span className="material-symbols-outlined" aria-hidden="true">delete</span>
                    </button>
                  </div>
                </div>
                <div className="summary-grid compact-summary">
                  {instanceSummaryFields(instance, summary).map(([label, value]) => (
                    <div key={label}><span>{label}</span><strong>{value}</strong></div>
                  ))}
                </div>
              </Card>
            );
          })
        )}
      </div>
      <div className="modal app-dialog" hidden={!createOpen}>
        <div className="modal-backdrop" onClick={() => setCreateOpen(false)}></div>
        <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="create-instance-title">
          <div className="modal-header">
            <div>
              <h2 id="create-instance-title">Ajouter une instance</h2>
              <p>Choisissez un type d’agent et un nom affiché. La connexion Gmail se fait ensuite depuis l’onboarding de l’instance.</p>
            </div>
            <button className="ghost icon-button" type="button" aria-label="Fermer" onClick={() => setCreateOpen(false)}>
              <span className="material-symbols-outlined" aria-hidden="true">close</span>
            </button>
          </div>
          <form className="login-form" onSubmit={handleCreate}>
            <label>
              <span>Type d’agent</span>
              <select value={createForm.agentType} onChange={(e) => setCreateForm({ ...createForm, agentType: e.target.value })}>
                {agentTypes.map((type) => <option key={type.id} value={type.id}>{type.display_name || type.id}</option>)}
              </select>
            </label>
            <label>
              <span>Nom affiché</span>
              <input
                value={createForm.displayName}
                onChange={(e) => setCreateForm({ ...createForm, displayName: e.target.value })}
                placeholder="Support"
                className={createError && !createForm.displayName.trim() ? 'input-error' : ''}
                autoFocus
                required
              />
            </label>
            <label>
              <span>Attribuer à</span>
              <select value={createForm.assignedTo} onChange={(e) => setCreateForm({ ...createForm, assignedTo: e.target.value })}>
                <option value="">Personne pour l’instant</option>
                {(usersQuery.data || []).map((user) => (
                  <option key={user.username} value={user.username}>{user.username}</option>
                ))}
              </select>
              <small className="muted">Sans attribution, seuls les administrateurs globaux verront cette instance.</small>
            </label>
            {createError && <p className="form-error">{createError}</p>}
            <div className="dialog-actions">
              <button type="button" onClick={() => setCreateOpen(false)}>Annuler</button>
              <button className="primary" type="submit" disabled={createInstance.isPending}>
                <span className="material-symbols-outlined" aria-hidden="true">add</span>
                <span>Créer l’instance</span>
              </button>
            </div>
          </form>
        </section>
      </div>
    </>
  );
}
