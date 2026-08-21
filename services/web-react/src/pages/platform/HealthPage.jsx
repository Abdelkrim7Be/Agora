import { useEffect, useMemo, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge } from '../../components/ui/Badge';
import { MetricTile } from '../../components/ui/MetricTile';
import { useStatus } from '../../contexts/StatusContext';
import { useInstance, isInstanceActive } from '../../contexts/InstanceContext';
import { formatDateTimeFr, HEALTH_STATUS_LABELS, healthPillClass, formatCountFr, formatPercentFr, formatDurationFr } from '../../utils/format';
import { useAgentInstancesQuery, useHealthQuery } from '../../api/queries';

const COMPONENTS = [
  { key: 'agent', label: 'Agent' },
  { key: 'poller', label: 'Poller' },
  { key: 'security', label: 'Sécurité' },
  { key: 'database', label: 'Base de données' },
  { key: 'redis', label: 'Redis' },
];

const HEALTH_READ_ROLES = new Set(['owner', 'approver', 'viewer']);

export default function HealthPage() {
  const { setStatus } = useStatus();
  const { instances, instanceId, setInstances } = useInstance();
  const [period, setPeriod] = useState('week');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);
  const instancesQuery = useAgentInstancesQuery();
  const availableInstances = instancesQuery.data || [];
  const healthInstance = useMemo(() => {
    const readable = (availableInstances || []).filter((instance) => (
      isInstanceActive(instance) && HEALTH_READ_ROLES.has(String(instance.effective_role || '').toLowerCase())
    ));
    return readable.find((instance) => instance.id === instanceId) || readable[0] || null;
  }, [availableInstances, instanceId]);
  const query = useHealthQuery(period, healthInstance?.id);

  useEffect(() => {
    if (instancesQuery.data) setInstances(instancesQuery.data);
  }, [instancesQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // Announced once: with a 15 s poll this used to repaint the status line forever.
  useEffect(() => {
    if (query.data && !announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Santé système à jour.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!healthInstance || !query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger la santé système : ${query.error.message}`, 'error');
  }, [healthInstance, query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const data = query.data?.health;
  const activity = query.data?.activity;
  const totals = activity?.totals || {};
  const workflows = (activity?.by_workflow || []).slice(0, 5);
  const scopeInstance = availableInstances.find((item) => item.id === (activity?.agent_instance_id || healthInstance?.id));
  const scopeLabel = scopeInstance?.display_name || activity?.agent_instance_id || healthInstance?.id;

  const budget = data?.gmail_budget || {};
  const budgetPct = Number(budget.pct || 0);
  const lastPoll = data?.poller?.last_poll_at ? formatDateTimeFr(data.poller.last_poll_at) : 'n/d';
  const notifyEnabled = Boolean(data?.notifications?.enabled);

  return (
    <>
      <PageHeading view="health" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Santé système</h2>
            <div className="meta"><span>Agent, poller, sécurité, base de données, file d'attente</span></div>
          </div>
          <div className="health-refresh">
            <span className="counter">{query.isFetching ? 'Actualisation…' : 'Actualisé toutes les 15 s'}</span>
            <button type="button" disabled={query.isFetching} onClick={() => query.refetch()}>
              <span className="material-symbols-outlined" aria-hidden="true">sync</span>
              <span>Actualiser</span>
            </button>
          </div>
        </div>
        {data && (
          <div id="health-tiles" className="metrics-grid" aria-label="État des composants">
            {COMPONENTS.map(({ key, label }) => {
              const component = data[key] || {};
              const status = component.status || 'unknown';
              return (
                <MetricTile
                  key={key}
                  label={label}
                  value={<StatusBadge status={status} label={HEALTH_STATUS_LABELS[status] || status} classFn={healthPillClass} />}
                />
              );
            })}
            <MetricTile label="Dernier sondage" value={lastPoll} size="sm" />
            <MetricTile label="File d'attente" value={String(data.queue_depth ?? 0)} />
            {budget.budget ? (
              <MetricTile
                label="Appels Gmail (dernière heure)"
                value={
                  <span className={`status-pill ${budgetPct >= 80 ? 'warn' : 'ok'}`}>
                    {budget.calls_last_hour ?? 0} / {budget.budget} ({budgetPct}%)
                  </span>
                }
              />
            ) : null}
            <MetricTile
              label="Notifications e-mail"
              value={<span className={`status-pill ${notifyEnabled ? 'ok' : 'warn'}`}>{notifyEnabled ? 'Activé' : 'Désactivé'}</span>}
            />
          </div>
        )}
        {!healthInstance && instancesQuery.isSuccess ? (
          <div className="notice">Aucune instance active avec accès lecture n’est disponible pour cette vue.</div>
        ) : null}
        {healthInstance && query.error && !data ? (
          <div className="notice">Impossible de charger la santé système : {query.error.message}</div>
        ) : null}
      </Card>
      <Card>
        <div className="card-header">
          <div>
            <h2>Activité</h2>
            <div className="meta"><span>{scopeLabel ? `Activité de « ${scopeLabel} »` : 'Volume traité par l\'instance sélectionnée'}</span></div>
          </div>
          <select aria-label="Période d'activité" value={period} onChange={(event) => setPeriod(event.target.value)}>
            <option value="day">Aujourd'hui</option>
            <option value="week">7 derniers jours</option>
            <option value="month">30 derniers jours</option>
          </select>
        </div>
        {healthInstance ? (
          <>
            <div className="cost-summary-grid">
              <div><strong>{formatCountFr(totals.emails_handled)}</strong><span>E-mails traités</span></div>
              <div><strong>{formatCountFr(totals.pending)}</strong><span>En attente</span></div>
              <div><strong>{formatCountFr(totals.approved)}</strong><span>Approuvés</span></div>
              <div>
                <strong>{formatPercentFr(totals.approval_rate_pct)}</strong>
                <span>Taux d'approbation</span>
                {totals.approval_rate_pct === null || totals.approval_rate_pct === undefined ? (
                  <small className="metric-hint">Aucune validation décidée sur la période</small>
                ) : null}
              </div>
              <div>
                <strong>{formatDurationFr(totals.avg_turnaround_seconds)}</strong>
                <span>Délai moyen</span>
                {totals.avg_turnaround_seconds === null || totals.avg_turnaround_seconds === undefined ? (
                  <small className="metric-hint">Se calcule dès la première décision</small>
                ) : null}
              </div>
            </div>
            {workflows.length ? (
              <div className="muted" style={{ marginTop: 10 }}>
                {workflows.map((row) => {
                  const label = row.display_name || row.workflow || row.category || '';
                  const safe = (!label || label === 'uncategorized') ? 'Sans cas métier' : label;
                  return `${safe} : ${formatCountFr(row.count ?? row.total ?? 0)}`;
                }).join(' · ')}
              </div>
            ) : null}
          </>
        ) : (
          <div className="notice">L’activité est masquée tant qu’aucune instance lisible n’est disponible.</div>
        )}
      </Card>
    </>
  );
}
