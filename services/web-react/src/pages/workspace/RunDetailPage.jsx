import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { useStatus } from '../../contexts/StatusContext';
import { formatDateTimeFr, statusLabelFr, formatCount, formatCost, summarizeTraceError } from '../../utils/format';
import { useRunDetailQuery } from '../../api/queries';

const TRUST_LABELS = { TRUSTED: 'Fiable', UNTRUSTED: 'Non fiable', INTERNAL: 'Interne' };
const trustLabel = (trust) => TRUST_LABELS[trust] || trust || 'Inconnu';
const trustClass = (trust) => (trust === 'TRUSTED' ? 'ok' : trust === 'INTERNAL' ? '' : 'warn');

const CLASSIFICATION_LABELS = { benign: 'Bénin', suspicious: 'Suspect', malicious: 'Malveillant' };
const classificationLabel = (value) => CLASSIFICATION_LABELS[value] || value || 'Non classé';

/** A verdict from the quarantined classifier, read by a human who is deciding
 * whether to trust this message — not a debugging dump of the service call. */
function SecuritySummary({ security }) {
  if (!security) return <span className="muted">Aucun verdict de sécurité attaché.</span>;
  const fields = Object.entries(security.fields || {});
  return (
    <div className="security-summary">
      <div className="card-tags">
        <span className={`status-pill ${security.injection_detected ? 'error' : 'ok'}`}>
          {security.injection_detected ? 'Injection détectée' : 'Aucune injection détectée'}
        </span>
        <span className={`status-pill ${security.classification === 'benign' ? 'ok' : 'warn'}`}>
          {classificationLabel(security.classification)}
        </span>
        <span className={`status-pill ${trustClass(security.source_trust)}`}>
          Source : {trustLabel(security.source_trust)}
        </span>
        {security.classifier_unavailable ? (
          <span className="status-pill warn">Classificateur indisponible — repli sur les heuristiques</span>
        ) : null}
      </div>
      {security.reasons?.length ? (
        <ul className="security-reasons">
          {security.reasons.map((reason, index) => <li key={index}>{reason}</li>)}
        </ul>
      ) : null}
      {fields.length ? (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Champ</th><th>Valeur</th><th>Confiance</th></tr></thead>
            <tbody>
              {fields.map(([key, field]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>{field?.value ?? ''}</td>
                  <td><span className={`status-pill ${trustClass(field?.trust)}`}>{trustLabel(field?.trust)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <details className="data-error-detail">
        <summary>Voir le JSON brut</summary>
        <pre>{JSON.stringify(security, null, 2)}</pre>
      </details>
    </div>
  );
}

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
              {/* A run stopped by the junk gate never reached the graph, so it has
                  no email state — the registry still knows who wrote and about what. */}
              <h2>{detail.email?.subject || detail.subject || 'Exécution sans objet'}</h2>
              <div className="meta">
                <span>{detail.email?.author || detail.author || 'Expéditeur inconnu'}</span>
                <span className="status-pill warn">{statusLabelFr(detail.status)}</span>
                <span>{statusLabelFr(detail.classification || 'unclassified')}</span>
                {detail.category_display_name ? <span>{detail.category_display_name}</span> : null}
              </div>
              {detail.junk_reason ? (
                <p className="review-reason">
                  <strong>Filtré automatiquement :</strong> {detail.junk_reason}. L’agent n’a pas
                  été sollicité pour ce message.
                </p>
              ) : null}
            </div>
            <span className="badge">{detail.run_id}</span>
          </div>

          <h3>Sécurité</h3>
          <SecuritySummary security={detail.security} />

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
                      <td>{formatDateTimeFr(item.started_at)}</td>
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
