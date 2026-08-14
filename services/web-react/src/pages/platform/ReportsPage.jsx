import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { TablePager } from '../../components/ui/TablePager';
import { usePagination } from '../../hooks/usePagination';
import { useStatus } from '../../contexts/StatusContext';
import { useReportsQuery, useUpdateReportStatus } from '../../api/queries';
import { formatDateTimeFr, statusLabelFr } from '../../utils/format';

const STATUS_FILTERS = [
  { value: '', label: 'Tous' },
  { value: 'open', label: 'Ouverts' },
  { value: 'acknowledged', label: 'Pris en compte' },
  { value: 'resolved', label: 'Résolus' },
];

const SEVERITY_LABEL_FR = { low: 'Faible', medium: 'Moyenne', high: 'Élevée' };

function severityClass(severity) {
  if (severity === 'high') return 'error';
  if (severity === 'medium') return 'warn';
  return '';
}

export default function ReportsPage() {
  const { setStatus } = useStatus();
  const [statusFilter, setStatusFilter] = useState('');
  const [updatingId, setUpdatingId] = useState('');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useReportsQuery(statusFilter);
  const updateStatus = useUpdateReportStatus();
  const reports = query.data || [];
  const pager = usePagination(reports);

  useEffect(() => {
    if (!query.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      const count = reports.length;
      setStatus(`${count} signalement${count === 1 ? '' : 's'} chargé${count === 1 ? '' : 's'}.`, 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les signalements : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleUpdate = async (id, status) => {
    setUpdatingId(id);
    try {
      await updateStatus.mutateAsync({ id, status });
      setStatus('Signalement mis à jour.', 'ok');
    } catch (error) {
      setStatus(`Impossible de mettre à jour le signalement : ${error.message}`, 'error');
    } finally {
      setUpdatingId('');
    }
  };

  return (
    <>
      <PageHeading view="reports" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Signalements</h2>
            <div className="meta"><span>Problèmes remontés par les utilisateurs de la plateforme</span></div>
          </div>
          <div className="toolbar">
            {STATUS_FILTERS.map((filter) => (
              <button
                key={filter.value || 'all'}
                type="button"
                className={statusFilter === filter.value ? 'primary' : 'ghost'}
                onClick={() => setStatusFilter(filter.value)}
              >
                {filter.label}
              </button>
            ))}
            <button type="button" disabled={query.isFetching} onClick={() => query.refetch()}>
              {query.isFetching ? 'Actualisation…' : 'Actualiser'}
            </button>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Sujet</th>
                <th>Auteur</th>
                <th>Gravité</th>
                <th>Suggestion IA</th>
                <th>Statut</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {query.error ? (
                <tr><td colSpan={7} className="empty-cell">Signalements indisponibles : {query.error.message}</td></tr>
              ) : !reports.length ? (
                <tr><td colSpan={7} className="empty-cell">Aucun signalement.</td></tr>
              ) : pager.visible.map((report) => (
                <tr key={report.id}>
                  <td>{formatDateTimeFr(report.createdAt)}</td>
                  <td>
                    <strong title={report.description}>{report.subject}</strong>
                    {report.contextPage ? <div className="muted">{report.contextPage}</div> : null}
                  </td>
                  <td>{report.reporterUsername}<div className="muted">{report.reporterRole || ''}</div></td>
                  <td>
                    <span className={`status-pill ${severityClass(report.severity)}`}>{SEVERITY_LABEL_FR[report.severity] || report.severity}</span>
                    {report.aiSeverity && report.aiSeverity !== report.severity ? (
                      <div className="muted" title="Gravité estimée par l'IA">IA : {SEVERITY_LABEL_FR[report.aiSeverity] || report.aiSeverity}</div>
                    ) : null}
                  </td>
                  <td className="report-suggestion-cell">
                    {report.aiSuggestion ? report.aiSuggestion : <span className="muted">—</span>}
                  </td>
                  <td><span className={`status-pill ${report.status === 'resolved' ? 'ok' : report.status === 'acknowledged' ? 'warn' : ''}`}>{statusLabelFr(report.status)}</span></td>
                  <td>
                    <div className="toolbar">
                      {report.status !== 'acknowledged' && report.status !== 'resolved' ? (
                        <button type="button" disabled={updatingId === report.id} onClick={() => handleUpdate(report.id, 'acknowledged')}>
                          Prendre en compte
                        </button>
                      ) : null}
                      {report.status !== 'resolved' ? (
                        <button type="button" disabled={updatingId === report.id} onClick={() => handleUpdate(report.id, 'resolved')}>
                          Résoudre
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {reports.length ? (
          <TablePager
            page={pager.page}
            pageCount={pager.pageCount}
            total={pager.total}
            size={pager.size}
            onPage={pager.setPage}
            onSize={pager.setSize}
            unit="signalements"
          />
        ) : null}
      </Card>
    </>
  );
}
