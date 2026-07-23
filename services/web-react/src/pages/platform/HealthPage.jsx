import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge } from '../../components/ui/Badge';
import { MetricTile } from '../../components/ui/MetricTile';
import { useStatus } from '../../contexts/StatusContext';
import { useInstance } from '../../contexts/InstanceContext';
import { HEALTH_STATUS_LABELS, healthPillClass, formatCountFr, formatPercentFr, formatDurationFr } from '../../utils/format';
import { useHealthQuery } from '../../api/queries';

const COMPONENTS = [
  { key: 'agent', label: 'Agent' },
  { key: 'poller', label: 'Poller' },
  { key: 'security', label: 'Sécurité' },
  { key: 'database', label: 'Base de données' },
  { key: 'redis', label: 'Redis' },
];

export default function HealthPage() {
  const { setStatus } = useStatus();
  const { instances, instanceId } = useInstance();
  const [period, setPeriod] = useState('week');
  const query = useHealthQuery(period);

  useEffect(() => {
    if (query.data) setStatus('Santé système actualisée.', 'ok');
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger la santé système : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const data = query.data?.health;
  const activity = query.data?.activity;
  const totals = activity?.totals || {};
  const workflows = (activity?.by_workflow || []).slice(0, 5);
  const scopeInstance = instances.find((item) => item.id === (activity?.agent_instance_id || instanceId));
  const scopeLabel = scopeInstance?.display_name || activity?.agent_instance_id || instanceId;

  const budget = data?.gmail_budget || {};
  const budgetPct = Number(budget.pct || 0);
  const lastPoll = data?.poller?.last_poll_at ? new Date(data.poller.last_poll_at).toLocaleString() : '—';
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
          <button type="button" onClick={() => query.refetch()}>
            <span className="material-symbols-outlined" aria-hidden="true">sync</span>
            <span>Actualiser</span>
          </button>
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
            <MetricTile label="Dernier sondage" value={lastPoll} />
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
              label="Notifications"
              value={<span className={`status-pill ${notifyEnabled ? 'ok' : 'warn'}`}>{notifyEnabled ? 'Activées' : 'Désactivées'}</span>}
            />
          </div>
        )}
        {query.error && <div className="notice">Impossible de charger la santé système : {query.error.message}</div>}
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
        <div className="cost-summary-grid">
          <div><strong>{formatCountFr(totals.emails_handled)}</strong><span>E-mails traités</span></div>
          <div><strong>{formatCountFr(totals.pending)}</strong><span>En attente</span></div>
          <div><strong>{formatCountFr(totals.approved)}</strong><span>Approuvés</span></div>
          <div><strong>{formatPercentFr(totals.approval_rate_pct)}</strong><span>Taux d'approbation</span></div>
          <div><strong>{formatDurationFr(totals.avg_turnaround_seconds)}</strong><span>Délai moyen</span></div>
        </div>
        {workflows.length ? (
          <div className="muted" style={{ marginTop: 10 }}>
            {workflows.map((row) => {
              const label = row.display_name || row.workflow || row.category || '';
              const safe = (!label || label === 'uncategorized') ? 'Sans workflow' : label;
              return `${safe} : ${formatCountFr(row.count ?? row.total ?? 0)}`;
            }).join(' · ')}
          </div>
        ) : null}
      </Card>
    </>
  );
}
