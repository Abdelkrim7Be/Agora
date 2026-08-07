import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { VerticalBarChart, HorizontalBarChart } from '../../components/ui/AnalyticsCharts';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useAnalyticsQuery } from '../../api/queries';
import { formatCountFr, formatPercentFr, formatDurationFr, workflowLabelFr } from '../../utils/format';

export default function DashboardPage() {
  const { instanceId } = useInstance();
  const { setStatus } = useStatus();
  const [period, setPeriod] = useState('week');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useAnalyticsQuery(period);
  const summary = query.data;
  const totals = summary?.totals || {};

  useEffect(() => {
    if (!summary) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Tableau de bord à jour.', 'ok');
    }
  }, [summary]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger le tableau de bord : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const workflows = summary?.by_workflow || [];
  const depts = summary?.dept_load || [];

  // A freshly onboarded mailbox renders four zeros, which reads as a measured
  // result rather than "nothing has run yet". Only say so once the request has
  // actually succeeded — during a load or a failure, zero means neither.
  const nothingProcessedYet = query.isSuccess
    && !totals.emails_handled
    && !totals.pending
    && workflows.length === 0;

  return (
    <>
      <PageHeading view="analytics" />
      <div className="toolbar">
        <select aria-label="Période du tableau de bord" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="day">Aujourd'hui</option>
          <option value="week">7 derniers jours</option>
          <option value="month">Mois en cours</option>
        </select>
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Actualiser</span>
        </button>
        <span className="counter">{summary?.agent_instance_id || instanceId}</span>
      </div>
      <div className="cost-summary-grid dashboard-summary-grid">
        <div><strong>{formatCountFr(totals.emails_handled)}</strong><span>E-mails traités</span></div>
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
        <div><strong>{formatCountFr(totals.pending)}</strong><span>En attente</span></div>
      </div>
      {nothingProcessedYet ? (
        <div className="notice dashboard-notice">
          <strong>Aucun e-mail traité pour l’instant.</strong> Les compteurs ci-dessus sont à zéro parce que
          l’agent n’a encore rien traité sur cette période, pas parce qu’une mesure vaut zéro. Il analyse la
          boîte à chaque relève ; les premiers chiffres apparaîtront après le prochain message reçu.
          Élargissez la période pour voir une activité plus ancienne.
        </div>
      ) : (
        <div className="notice dashboard-notice">
          Ce tableau de bord mesure l’activité de l’agent sélectionné. Les e-mails sortants restent bloqués tant qu’une validation humaine est requise.
        </div>
      )}
      <div className="cost-layout dashboard-chart-layout">
        <Card className="dashboard-chart-card">
          <h2 className="section-title">Volume traité</h2>
          <VerticalBarChart points={summary?.volume_timeline || []} emptyLabel="Aucune donnée pour cette période." />
        </Card>
        <Card className="dashboard-chart-card">
          <h2 className="section-title">Cas métier les plus actifs</h2>
          <HorizontalBarChart rows={summary?.top_categories || []} emptyLabel="Aucune donnée pour cette période." />
        </Card>
      </div>
      <div className="cost-layout cost-recent">
        <div>
          <h2 className="section-title">Par cas métier</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr><th>Cas métier</th><th>Volume</th><th>En attente</th><th>Approuvés</th><th>Rejetés</th><th>Délai moyen</th></tr>
              </thead>
              <tbody>
                {workflows.length ? workflows.map((row, i) => (
                  <tr key={row.category ?? row.display_name ?? i}>
                    <td>{workflowLabelFr(row)}</td>
                    <td>{formatCountFr(row.count)}</td>
                    <td>{formatCountFr(row.pending)}</td>
                    <td>{formatCountFr(row.approved)}</td>
                    <td>{formatCountFr(row.rejected)}</td>
                    <td>{formatDurationFr(row.avg_turnaround_seconds)}</td>
                  </tr>
                )) : <tr><td colSpan={6} className="empty-cell">Aucun cas métier sur cette période.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h2 className="section-title">Charge par département</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr><th>Département</th><th>Volume</th><th>En attente</th><th>Approuvés</th><th>Rejetés</th></tr>
              </thead>
              <tbody>
                {depts.length ? depts.map((row, i) => (
                  <tr key={row.dept ?? i}>
                    <td>{row.dept || 'Sans département'}</td>
                    <td>{formatCountFr(row.count)}</td>
                    <td>{formatCountFr(row.pending)}</td>
                    <td>{formatCountFr(row.approved)}</td>
                    <td>{formatCountFr(row.rejected)}</td>
                  </tr>
                )) : <tr><td colSpan={5} className="empty-cell">Aucune charge départementale sur cette période.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
