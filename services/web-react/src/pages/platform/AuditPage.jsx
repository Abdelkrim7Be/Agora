import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { TablePager } from '../../components/ui/TablePager';
import { outcomeClass, compactText } from '../../utils/format';
import { useStatus } from '../../contexts/StatusContext';
import { useAuditQuery, useVerifyAuditChain } from '../../api/queries';

const TECHNICAL_NOISE_PATHS = new Set([
  '/api/agent/runs',
  '/api/agent/health',
  '/api/agent/analytics',
  '/api/agent/metrics',
  '/api/agent/sync/status',
  '/api/agent/instance-setup',
  '/api/agent/events',
  '/api/agent/notifications',
  '/api/agent/notifications/unread-count',
]);

function csvCell(value) {
  return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

function normalizedPath(path) {
  if (!path) return '';
  return String(path).split('?')[0];
}

function isTechnicalNoise(event) {
  return String(event.method || '').toUpperCase() === 'GET'
    && TECHNICAL_NOISE_PATHS.has(normalizedPath(event.path));
}

export default function AuditPage() {
  const { setStatus } = useStatus();
  const [page, setPage] = useState(0);
  const [limit, setLimit] = useState(100);
  const [search, setSearch] = useState('');
  const [showTechnicalNoise, setShowTechnicalNoise] = useState(false);
  const query = useAuditQuery(page, limit, showTechnicalNoise);
  const verifyChain = useVerifyAuditChain();

  useEffect(() => {
    if (query.data) setStatus(`Page d’audit ${page + 1} chargée.`, 'ok');
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger l’audit : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const copyPath = async (path) => {
    if (!path) return;
    await navigator.clipboard?.writeText(path);
    setStatus('Chemin copié.', 'ok');
  };

  const events = query.data?.events || [];
  const hasMore = query.data?.hasMore || false;
  const queryLower = search.toLowerCase();
  const searchFiltered = events.filter((event) => {
    const haystack = [event.username, event.role, event.action, event.method, event.path, event.outcome, String(event.upstreamStatus || '')]
      .join(' ')
      .toLowerCase();
    return haystack.includes(queryLower);
  });
  const hiddenNoiseCount = searchFiltered.filter(isTechnicalNoise).length;
  const filtered = showTechnicalNoise
    ? searchFiltered
    : searchFiltered.filter((event) => !isTechnicalNoise(event));

  const exportCsv = () => {
    if (!filtered.length) {
      setStatus('Aucun événement visible à exporter.', 'error');
      return;
    }
    const header = ['timestamp', 'username', 'role', 'action', 'method', 'path', 'upstreamStatus', 'outcome'];
    const rows = filtered.map((event) => header.map((key) => csvCell(event[key])).join(','));
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
        <button
          type="button"
          onClick={() => {
            verifyChain.reset();
            verifyChain.mutate(undefined, {
              onSuccess: (result) => {
                setStatus(
                  result.valid
                    ? `Chaîne d’audit intègre (${result.verifiedCount} événements vérifiés).`
                    : `Chaîne d’audit rompue à l’événement #${result.brokenAtId}.`,
                  result.valid ? 'ok' : 'error',
                );
              },
              onError: (error) => setStatus(`Vérification impossible : ${error.message}`, 'error'),
            });
          }}
          disabled={verifyChain.isPending}
        >
          <span className="material-symbols-outlined" aria-hidden="true">verified</span>
          <span>{verifyChain.isPending ? 'Vérification…' : 'Vérifier l’intégrité'}</span>
        </button>
        <label className="toolbar-toggle audit-noise-toggle" title="Afficher les sondages automatiques de statut et de notifications">
          <input
            type="checkbox"
            checked={showTechnicalNoise}
            onChange={(event) => {
              setShowTechnicalNoise(event.target.checked);
              setPage(0);
            }}
          />
          <span>Bruit technique</span>
        </label>
        <span className="toolbar-spacer"></span>
        <span className="counter">
          {filtered.length} événement{filtered.length === 1 ? '' : 's'} sur cette page
          {!showTechnicalNoise && hiddenNoiseCount > 0 ? (
            <span className="audit-hidden-count"> · {hiddenNoiseCount} masqué{hiddenNoiseCount === 1 ? '' : 's'}</span>
          ) : null}
        </span>
      </div>
      {verifyChain.data ? (
        <p className="empty-cell">
          <span className={`status-pill ${verifyChain.data.valid ? 'ok' : 'error'}`}>
            {verifyChain.data.valid ? 'Chaîne intègre' : 'Chaîne rompue'}
          </span>{' '}
          {verifyChain.data.valid
            ? `${verifyChain.data.verifiedCount} événements vérifiés, ${verifyChain.data.unverifiableLegacyCount} antérieurs à la chaîne (non vérifiables).`
            : `rompue à l’événement #${verifyChain.data.brokenAtId} — ${verifyChain.data.reason || 'raison inconnue'}.`}
        </p>
      ) : null}
      <div className="table-wrap">
        <table className="data-table audit-table">
          <thead>
            <tr>
              <th>Heure</th><th>Utilisateur</th><th>Rôle</th><th>Action</th>
              <th>Méthode</th><th>Chemin</th><th className="col-center">Statut</th><th>Résultat</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length ? filtered.map((event, index) => (
              <tr key={index}>
                <td>{event.timestamp || '—'}</td>
                <td>{event.username || '—'}</td>
                <td><span className="status-pill">{event.role || '—'}</span></td>
                <td>{event.action || '—'}</td>
                <td>{event.method || '—'}</td>
                <td>
                  {event.path ? (
                    <span className="audit-path-cell">
                      <span title={event.path}>{compactText(event.path, 10)}</span>
                      <button
                        type="button"
                        className="ghost icon-button audit-path-copy"
                        aria-label={`Copier le chemin ${event.path}`}
                        title="Copier le chemin"
                        onClick={() => copyPath(event.path)}
                      >
                        <span className="material-symbols-outlined" aria-hidden="true">content_copy</span>
                      </button>
                    </span>
                  ) : '—'}
                </td>
                {/* Not every audited action is proxied to an agent (login,
                    user management) — those legitimately have no upstream
                    status. A blank cell there read as a rendering glitch;
                    a dash reads as "not applicable". */}
                <td className="col-center">{event.upstreamStatus ?? '—'}</td>
                <td>
                  <span
                    className={`status-pill ${outcomeClass(event.outcome, event.upstreamStatus)}`}
                    title={event.outcome || ''}
                  >
                    {event.outcome ? compactText(event.outcome, 18) : '—'}
                  </span>
                </td>
              </tr>
            )) : (
              <tr><td colSpan={8} className="empty-cell">Aucun événement d’audit ne correspond.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {/* The gateway answers a page at a time and never a total, so there is no
          honest page count to render here — only whether another page exists. */}
      <TablePager
        page={page}
        size={limit}
        hasMore={hasMore}
        onPage={(next) => setPage(Math.max(0, next))}
      />
    </>
  );
}
