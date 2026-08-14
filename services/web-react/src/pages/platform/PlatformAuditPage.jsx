import { useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge } from '../../components/ui/Badge';
import { useStatus } from '../../contexts/StatusContext';
import { useRunPlatformAudit, useAuditRunsQuery, useAuditRunDetailQuery } from '../../api/queries';
import { formatDateTimeFr } from '../../utils/format';

const VERDICT_LABEL_FR = { ok: 'Sain', warn: 'Avertissement', critical: 'Critique' };
const VERDICT_CLASS = { ok: 'ok', warn: 'warn', critical: 'error' };
const COMPONENT_LABEL_FR = { agent: 'Agent', poller: 'Sondeur', security: 'Sécurité', database: 'Base de données', redis: 'Redis' };
const COMPONENT_ICON = { agent: 'smart_toy', poller: 'sync', security: 'shield', database: 'database', redis: 'bolt' };
const COMPONENT_STATUS_CLASS = { up: 'ok', down: 'error', paused: 'warn', disabled: '' };

function VerdictBadge({ verdict }) {
  return <StatusBadge status={verdict} label={VERDICT_LABEL_FR[verdict] || verdict} classFn={() => VERDICT_CLASS[verdict] || ''} />;
}

function ComponentPills({ components }) {
  const entries = Object.entries(components || {});
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <div className="audit-component-pills">
      {entries.map(([key, status]) => (
        <span key={key} className={`status-pill compact ${COMPONENT_STATUS_CLASS[status] ?? ''}`.trim()}>
          <span className="material-symbols-outlined" aria-hidden="true">{COMPONENT_ICON[key] || 'settings'}</span>
          <span>{COMPONENT_LABEL_FR[key] || key}</span>
        </span>
      ))}
    </div>
  );
}

function DlqPill({ pending }) {
  return (
    <span className={`status-pill compact ${pending > 0 ? 'warn' : 'ok'}`}>
      {pending > 0 ? `${pending} en attente` : 'Aucune'}
    </span>
  );
}

function WarningChips({ warnings }) {
  if (!warnings?.length) return <span className="muted">—</span>;
  return (
    <div className="audit-component-pills">
      {warnings.map((warning, index) => (
        <span key={index} className="status-pill compact warn">{warning}</span>
      ))}
    </div>
  );
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function parseDetails(raw) {
  if (raw == null) return null;
  if (typeof raw !== 'string') return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function RunDetailModal({ runId, onClose }) {
  const { setStatus } = useStatus();
  const detailQuery = useAuditRunDetailQuery(runId);
  const detail = detailQuery.data;
  const parsed = detail ? parseDetails(detail.details) : null;

  const handleDownload = () => {
    if (!detail) return;
    downloadJson(`agora-audit-run-${runId}.json`, { ...detail, details: parsed });
    setStatus('Rapport d’audit téléchargé.', 'ok');
  };

  return (
    <div className="modal">
      <div className="modal-backdrop" onClick={onClose}></div>
      <section className="modal-panel wide" role="dialog" aria-modal="true" aria-labelledby="audit-run-detail-title">
        <div className="modal-header">
          <div>
            <h2 id="audit-run-detail-title">Audit du {detail ? formatDateTimeFr(detail.ran_at) : '…'}</h2>
            <p>{detail ? `Lancé par ${detail.ran_by || 'n/d'}` : 'Chargement du rapport…'}</p>
          </div>
          <button className="ghost icon-button" type="button" aria-label="Fermer" onClick={onClose}>
            <span className="material-symbols-outlined" aria-hidden="true">close</span>
          </button>
        </div>
        {detailQuery.isLoading ? <p className="muted">Chargement…</p> : null}
        {detailQuery.error ? <p className="notice error">Impossible de charger ce rapport : {detailQuery.error.message}</p> : null}
        {detail ? (
          <>
            <div className="audit-result-summary">
              <VerdictBadge verdict={detail.verdict} />
            </div>
            <pre className="audit-run-detail-json">{JSON.stringify(parsed, null, 2)}</pre>
          </>
        ) : null}
        <div className="dialog-actions">
          <button className="primary" type="button" disabled={!detail} onClick={handleDownload}>
            <span className="material-symbols-outlined" aria-hidden="true">download</span>
            <span>Télécharger le rapport</span>
          </button>
        </div>
      </section>
    </div>
  );
}

export default function PlatformAuditPage() {
  const { setStatus } = useStatus();
  const [result, setResult] = useState(null);
  const [openRunId, setOpenRunId] = useState(null);
  const runAudit = useRunPlatformAudit();
  const historyQuery = useAuditRunsQuery();
  const history = historyQuery.data || [];

  const handleRun = async () => {
    setStatus('Audit de la plateforme en cours…');
    try {
      const outcome = await runAudit.mutateAsync();
      setResult(outcome);
      const message = outcome.verdict === 'ok'
        ? 'Plateforme saine : aucune anomalie détectée.'
        : `Audit terminé : verdict ${VERDICT_LABEL_FR[outcome.verdict] || outcome.verdict}.`;
      setStatus(message, outcome.verdict === 'ok' ? 'ok' : outcome.verdict === 'critical' ? 'error' : 'warn');
    } catch (error) {
      setStatus(`Impossible de lancer l'audit : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="platformAudit" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Audit de la plateforme</h2>
            <div className="meta"><span>Vérifie chaque instance d'agent (santé, file d'erreurs) et l'accès à la base de la plateforme, en un clic</span></div>
          </div>
          <button className="primary" type="button" disabled={runAudit.isPending} onClick={handleRun}>
            <span className="material-symbols-outlined" aria-hidden="true">health_and_safety</span>
            <span>{runAudit.isPending ? 'Audit en cours…' : 'Auditer la plateforme'}</span>
          </button>
        </div>

        {result ? (
          <div className="audit-result">
            <div className="audit-result-summary">
              <VerdictBadge verdict={result.verdict} />
              <span className="muted">{result.instanceCount} instance{result.instanceCount === 1 ? '' : 's'} · {result.warningCount} avertissement{result.warningCount === 1 ? '' : 's'} · {formatDateTimeFr(result.ranAt)}</span>
            </div>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr><th>Instance</th><th>Statut</th><th>Composants</th><th>File d'erreurs</th><th>Avertissements</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <td><strong>Gateway (plateforme)</strong></td>
                    <td><VerdictBadge verdict={result.gatewayVerdict} /></td>
                    <td colSpan={2} className="muted">{result.gatewayNote}</td>
                    <td></td>
                  </tr>
                  {(result.instances || []).map((instance) => (
                    <tr key={instance.instanceId}>
                      <td>{instance.displayName || instance.instanceId}</td>
                      <td><VerdictBadge verdict={instance.verdict} /></td>
                      <td>
                        {instance.reachable
                          ? <ComponentPills components={instance.components} />
                          : <span className="status-pill compact error">Instance injoignable</span>}
                      </td>
                      <td><DlqPill pending={instance.dlqPending} /></td>
                      <td><WarningChips warnings={instance.warnings} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <p className="muted">Aucun audit exécuté pendant cette session. Cliquez sur « Auditer la plateforme » pour lancer une vérification.</p>
        )}
      </Card>

      <Card>
        <div className="card-header">
          <div>
            <h2>Historique</h2>
            <div className="meta"><span>20 derniers audits</span></div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Date</th><th>Lancé par</th><th>Verdict</th><th>Instances</th><th>Avertissements</th><th></th></tr></thead>
            <tbody>
              {!history.length ? (
                <tr><td colSpan={6} className="empty-cell">Aucun audit enregistré.</td></tr>
              ) : history.map((run) => (
                <tr key={run.id} className="row-clickable" onClick={() => setOpenRunId(run.id)}>
                  <td>{formatDateTimeFr(run.ranAt)}</td>
                  <td>{run.ranBy || 'n/d'}</td>
                  <td><VerdictBadge verdict={run.verdict} /></td>
                  <td>{run.instanceCount}</td>
                  <td>{run.warningCount}</td>
                  <td>
                    <button type="button" className="ghost" onClick={(e) => { e.stopPropagation(); setOpenRunId(run.id); }}>
                      <span className="material-symbols-outlined" aria-hidden="true">description</span>
                      <span>Détails</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {openRunId != null ? <RunDetailModal runId={openRunId} onClose={() => setOpenRunId(null)} /> : null}
    </>
  );
}
