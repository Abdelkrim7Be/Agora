import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge } from '../../components/ui/Badge';
import { EmptyState } from '../../components/ui/EmptyState';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance, isInstanceActive } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { roleAtLeast } from '../../utils/roles';
import { agentTypeLabel, instanceIdentity, instanceSummaryFields } from '../../utils/format';
import { useAgentInstancesQuery, useAgentTypesQuery, useRenameInstance, useDeleteInstance } from '../../api/queries';

function instanceStatus(instance) {
  return String(instance?.status || 'active').toLowerCase();
}

export default function InstancesPage() {
  const { globalRole } = useAuth();
  const { instances, setInstances, setInstanceId } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog, promptDialog } = useDialog();
  const navigate = useNavigate();
  const query = useAgentInstancesQuery();
  const typesQuery = useAgentTypesQuery();
  const renameInstance = useRenameInstance();
  const deleteInstance = useDeleteInstance();
  const canManage = roleAtLeast(globalRole, 'owner');
  const announcedInitialLoad = useRef(false);

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
    const needsSetup = setupStatus && !['ready', 'not_started', 'unknown'].includes(setupStatus);
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
        <button className="primary" type="button" disabled title="Assistant multi-étapes à venir">
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
            const renameTitle = canManage ? 'Renommer l’instance' : 'Rôle owner requis';
            const deleteTitle = canManage ? 'Supprimer l’instance' : 'Rôle owner requis';
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
    </>
  );
}
