import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { useStatus } from '../../contexts/StatusContext';
import { statusLabelFr, formatCount, formatCost, summarizeTraceError } from '../../utils/format';
import { useRunDetailQuery } from '../../api/queries';

export default function RunDetailPage() {
  const { runId } = useParams();
  const { setStatus } = useStatus();
  const query = useRunDetailQuery(runId);

  useEffect(() => {
    if (query.data) setStatus('Détail de l’exécution chargé.', 'ok');
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger le détail de l’exécution : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const detail = query.data;

  return (
    <>
      <PageHeading view="run-detail" />
      {!runId ? (
        <div className="detail-panel empty">Sélectionnez une exécution depuis la validation ou saisissez un ID.</div>
      ) : !detail ? (
        <div className="detail-panel empty">{query.error ? `Erreur : ${query.error.message}` : 'Chargement...'}</div>
      ) : (
        <div className="detail-panel card">
          <div className="card-header">
            <div>
              <h2>{detail.email?.subject || 'Exécution sans objet'}</h2>
              <div className="meta">
                <span>{detail.email?.author || 'Expéditeur inconnu'}</span>
                <span className="status-pill warn">{statusLabelFr(detail.status)}</span>
                <span>{statusLabelFr(detail.classification || 'unclassified')}</span>
              </div>
            </div>
            <span className="badge">{detail.run_id}</span>
          </div>

          <h3>Sécurité</h3>
          {detail.security ? <pre>{JSON.stringify(detail.security, null, 2)}</pre> : <span className="muted">Aucun verdict de sécurité attaché.</span>}

          <h3>Trace par nœud</h3>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr><th>Début</th><th>Nœud</th><th>Statut</th><th>Latence</th><th>Tokens</th><th>Coût</th><th>Erreur</th></tr>
              </thead>
              <tbody>
                {(detail.trace || []).length ? detail.trace.map((item, index) => {
                  const isHitlPause = /interrupt|action_request|pending_action/i.test(String(item.error || ''));
                  const pillClass = item.status === 'ok' ? 'ok' : (isHitlPause ? 'warn' : 'error');
                  const pillLabel = isHitlPause ? "En attente d'approbation" : statusLabelFr(item.status);
                  return (
                    <tr key={index}>
                      <td>{item.started_at ? new Date(item.started_at).toLocaleString() : '—'}</td>
                      <td>{item.node || 'unknown'}</td>
                      <td><span className={`status-pill ${pillClass}`}>{pillLabel}</span></td>
                      <td>{item.latency_ms ?? 0} ms</td>
                      <td>{formatCount(item.total_tokens || 0)}</td>
                      <td>{formatCost(item.cost_eur || 0)}</td>
                      <td>{summarizeTraceError(item.error)}</td>
                    </tr>
                  );
                }) : <tr><td colSpan={7} className="empty-cell">Aucune trace enregistrée.</td></tr>}
              </tbody>
            </table>
          </div>

          <h3>Chronologie</h3>
          <div className="timeline">
            {(detail.timeline || []).length ? detail.timeline.map((item, index) => (
              <div className="timeline-item" key={index}>
                <div className="timeline-index">{index + 1}</div>
                <div>
                  <strong>{item.role || 'message'}</strong>
                  {item.tool_calls?.length ? <span className="badge">{item.tool_calls.map((call) => call.name).join(', ')}</span> : null}
                  <pre>{String(item.content || '')}</pre>
                </div>
              </div>
            )) : <div className="empty">Aucun message dans la chronologie.</div>}
          </div>
        </div>
      )}
    </>
  );
}
