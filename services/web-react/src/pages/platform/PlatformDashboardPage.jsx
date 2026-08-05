import { useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge } from '../../components/ui/Badge';
import { EmptyState } from '../../components/ui/EmptyState';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance, isInstanceActive } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useAgentInstancesQuery, useAgentTypesQuery, useMailboxesQuery, useUsersQuery } from '../../api/queries';
import { agentTypeLabel, formatCostEur, formatCountFr, instanceIdentity } from '../../utils/format';

function countReady(instances) {
  return instances.filter((instance) => instance.summary?.setup_status === 'ready').length;
}

function countPending(instances) {
  return instances.reduce((total, instance) => total + Number(instance.summary?.pending_drafts || 0), 0);
}

export default function PlatformDashboardPage() {
  const { globalRole } = useAuth();
  const { instances, setInstances } = useInstance();
  const { setStatus } = useStatus();
  const instancesQuery = useAgentInstancesQuery();
  const typesQuery = useAgentTypesQuery();
  const mailboxesQuery = useMailboxesQuery();
  const usersQuery = useUsersQuery(globalRole === 'admin');
  const announcedInitialLoad = useRef(false);
  const isAdmin = globalRole === 'admin';

  useEffect(() => {
    if (!instancesQuery.data) return;
    setInstances(instancesQuery.data);
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Tableau de bord plateforme à jour.', 'ok');
    }
  }, [instancesQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (instancesQuery.error) {
      setStatus(`Impossible de charger la plateforme : ${instancesQuery.error.message}`, 'error');
    }
  }, [instancesQuery.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const visibleInstances = instancesQuery.data || instances;
  const mailboxes = mailboxesQuery.data || [];
  const users = usersQuery.data || [];
  const agentTypes = typesQuery.data || [];

  const totals = useMemo(() => {
    const dailyCost = visibleInstances.reduce((sum, instance) => (
      sum + Number(instance.summary?.today_cost_eur || 0)
    ), 0);
    return {
      instances: visibleInstances.length,
      ready: countReady(visibleInstances),
      active: visibleInstances.filter(isInstanceActive).length,
      pending: countPending(visibleInstances),
      mailboxes: mailboxes.length,
      users: users.length,
      dailyCost,
    };
  }, [mailboxes.length, users.length, visibleInstances]);

  const recentInstances = [...visibleInstances].slice(0, 4);

  return (
    <>
      <PageHeading view="platformDashboard" />
      <div className="platform-dashboard-grid">
        <Link className="platform-metric-card" to="/instances">
          <span>Instances IA</span>
          <strong>{formatCountFr(totals.instances)}</strong>
          <small>{formatCountFr(totals.ready)} prêtes · {formatCountFr(totals.active)} actives</small>
        </Link>
        <Link className="platform-metric-card" to="/instances">
          <span>Boîtes surveillées</span>
          <strong>{formatCountFr(totals.mailboxes)}</strong>
          <small>Connexions mail accessibles</small>
        </Link>
        <Link className="platform-metric-card" to="/instances">
          <span>À valider</span>
          <strong>{formatCountFr(totals.pending)}</strong>
          <small>Brouillons ou actions en attente</small>
        </Link>
        {isAdmin ? (
          <Link className="platform-metric-card" to="/team">
            <span>Utilisateurs</span>
            <strong>{formatCountFr(totals.users)}</strong>
            <small>Comptes plateforme</small>
          </Link>
        ) : (
          <Link className="platform-metric-card" to="/agent-types">
            <span>Catalogue</span>
            <strong>{formatCountFr(agentTypes.length)}</strong>
            <small>Types d’agents disponibles</small>
          </Link>
        )}
      </div>

      <div className="platform-dashboard-layout">
        <Card className="platform-overview-card">
          <div className="card-header">
            <div>
              <h2>Agents en service</h2>
              <div className="meta"><span>Les espaces de travail ouverts sur cette plateforme</span></div>
            </div>
            <Link className="button-link" to="/instances">
              <span className="material-symbols-outlined" aria-hidden="true">deployed_code</span>
              <span>Voir les instances</span>
            </Link>
          </div>
          <div className="platform-agent-list">
            {!recentInstances.length ? (
              <EmptyState message="Aucune instance d’agent pour le moment." />
            ) : recentInstances.map((instance) => (
              <Link className="platform-agent-row" to={`/instance/${encodeURIComponent(instance.id)}`} key={instance.id}>
                <span className="platform-agent-icon material-symbols-outlined" aria-hidden="true">mail</span>
                <span>
                  <strong>{instance.display_name || instance.id}</strong>
                  <small>{agentTypeLabel(instance.agent_type, agentTypes)} · {instanceIdentity(instance)}</small>
                </span>
                <StatusBadge status={instance.status || 'active'} />
              </Link>
            ))}
          </div>
        </Card>

        <Card className="platform-overview-card">
          <div className="card-header">
            <div>
              <h2>En attente de décision</h2>
              <div className="meta"><span>Toutes instances confondues</span></div>
            </div>
          </div>
          <div className="platform-control-panel">
            <Link to="/instances"><span>Actions à valider</span><strong>{formatCountFr(totals.pending)}</strong></Link>
            <Link to="/instances"><span>Coût du jour</span><strong>{formatCostEur(totals.dailyCost)}</strong></Link>
            <Link to="/instances"><span>Boîtes connectées</span><strong>{formatCountFr(totals.mailboxes)}</strong></Link>
          </div>
        </Card>
      </div>
    </>
  );
}
