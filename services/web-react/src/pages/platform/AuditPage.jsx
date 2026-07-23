import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Pager } from '../../components/ui/Pager';
import { outcomeClass } from '../../utils/format';
import { useStatus } from '../../contexts/StatusContext';
import { useAuditQuery } from '../../api/queries';

function csvCell(value) {
  return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

export default function AuditPage() {
  const { setStatus } = useStatus();
  const [page, setPage] = useState(0);
  const [limit, setLimit] = useState(100);
  const [search, setSearch] = useState('');
  const query = useAuditQuery(page, limit);

  useEffect(() => {
    if (query.data) setStatus(`Page d’audit ${page + 1} chargée.`, 'ok');
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger l’audit : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const events = query.data?.events || [];
  const hasMore = query.data?.hasMore || false;
  const queryLower = search.toLowerCase();
  const filtered = events.filter((event) => {
    const haystack = [event.username, event.role, event.action, event.method, event.path, event.outcome, String(event.upstreamStatus || '')]
      .join(' ')
      .toLowerCase();
    return haystack.includes(queryLower);
  });

  const exportCsv = () => {
    if (!events.length) {
      setStatus('Chargez les événements d’audit avant d’exporter.', 'error');
      return;
    }
    const header = ['timestamp', 'username', 'role', 'action', 'method', 'path', 'upstreamStatus', 'outcome'];
    const rows = events.map((event) => header.map((key) => csvCell(event[key])).join(','));
    const blob = new Blob([[header.join(','), ...rows].join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `agora-audit-page-${page + 1}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus('CSV d’audit exporté.', 'ok');
  };

  return (
    <>
      <PageHeading view="audit" />
      <div className="toolbar audit-toolbar">
        <input
          aria-label="Rechercher dans l'audit"
          placeholder="Rechercher action, utilisateur, chemin"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select aria-label="Limite d'audit" value={limit} onChange={(event) => { setLimit(Number(event.target.value)); setPage(0); }}>
          <option value={50}>50</option>
          <option value={100}>100</option>
          <option value={250}>250</option>
          <option value={500}>500</option>
        </select>
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span>
          <span>Charger l'audit</span>
        </button>
        <button type="button" onClick={exportCsv}>
          <span className="material-symbols-outlined" aria-hidden="true">download</span>
          <span>Export CSV</span>
        </button>
        <span className="toolbar-spacer"></span>
        <Pager
          className="audit-pager"
          page={page}
          hasMore={hasMore}
          onPrev={() => setPage((current) => Math.max(0, current - 1))}
          onNext={() => setPage((current) => current + 1)}
          countLabel={`${filtered.length} event${filtered.length === 1 ? '' : 's'}`}
        />
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Heure</th><th>Utilisateur</th><th>Rôle</th><th>Action</th>
              <th>Méthode</th><th>Chemin</th><th>Statut</th><th>Résultat</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length ? filtered.map((event, index) => (
              <tr key={index}>
                <td>{event.timestamp || ''}</td>
                <td>{event.username || ''}</td>
                <td><span className="status-pill">{event.role || ''}</span></td>
                <td>{event.action || ''}</td>
                <td>{event.method || ''}</td>
                <td>{event.path || ''}</td>
                <td>{String(event.upstreamStatus ?? '')}</td>
                <td><span className={`status-pill ${outcomeClass(event.outcome, event.upstreamStatus)}`}>{event.outcome || ''}</span></td>
              </tr>
            )) : (
              <tr><td colSpan={8} className="empty-cell">Aucun événement d’audit ne correspond.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
