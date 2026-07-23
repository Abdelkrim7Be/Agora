import { useState } from 'react';
import { useInstance } from '../../contexts/InstanceContext';
import { useI18n } from '../../contexts/I18nContext';
import { useSummarizeRun } from '../../api/queries';
import ActionArgsEditor from './ActionArgsEditor';
import FeedbackChat from './FeedbackChat';
import {
  actionArgs,
  redraftCapable,
  formatDateTimeFr,
  formatDurationFr,
  confidenceClass,
} from '../../utils/format';

const TONES = [
  { value: 'formel', label: 'Formel' },
  { value: 'court', label: 'Court' },
  { value: 'amical', label: 'Amical' },
];

function SlaBadges({ run }) {
  return (
    <>
      {run.sla_label ? <span className={`status-pill ${run.overdue ? 'error' : 'warn'}`}>SLA {run.sla_label}</span> : null}
      {run.overdue ? (
        <span className="status-pill error">En retard {formatDurationFr(run.overdue_by_seconds)}</span>
      ) : run.due_at ? (
        <span className="status-pill warn">Échéance {formatDateTimeFr(run.due_at)}</span>
      ) : null}
      {run.escalated_at ? <span className="status-pill warn">Escaladé vers {run.escalation_target || 'destinataire inconnu'}</span> : null}
    </>
  );
}

export default function ValidationCard({
  run,
  selected,
  onToggleSelect,
  editedFields,
  onFieldChange,
  feedbackMessages,
  feedbackDraft,
  onFeedbackDraftChange,
  busy,
  onDecision,
  isActive,
}) {
  const { hasRole } = useInstance();
  const { t } = useI18n();
  const canApprove = hasRole('approver');
  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const summarizeRun = useSummarizeRun();

  const args = actionArgs(run);
  const toneField = ['content', 'body', 'note'].find((key) => key in args);
  const badgeKey = run.action_type || 'unknown';
  const canRedraft = redraftCapable(run);

  const handleSummarize = async () => {
    setSummarizing(true);
    setSummary('Résumé en cours…');
    try {
      const result = await summarizeRun.mutateAsync(run.run_id);
      setSummary(result.summary || 'Aucun contenu à résumer.');
    } catch (error) {
      setSummary(`Résumé indisponible : ${error.message}`);
    } finally {
      setSummarizing(false);
    }
  };

  return (
    <article className={`card${isActive ? ' active-run' : ''}${busy ? ' card-resolving' : ''}`} data-card={run.run_id}>
      <div className="card-header">
        {canApprove && (
          <label className="bulk-select" title="Sélectionner pour une action groupée">
            <input type="checkbox" checked={selected} onChange={() => onToggleSelect(run.run_id)} />
          </label>
        )}
        <div>
          <h2>{run.subject || 'E-mail sans objet'}</h2>
          <div className="meta">
            <span>{run.author || 'Expéditeur inconnu'}</span>
            <button className="link-button" type="button" onClick={() => onDecision('detail')}>Voir le détail</button>
            <span>{run.updated_at || ''}</span>
            {run.assignee ? <span className="assignee-badge">👤 {run.assignee}</span> : null}
            <SlaBadges run={run} />
          </div>
        </div>
        <div className="card-tags">
          {run.confidence ? (
            <span className={`confidence-pill ${confidenceClass(run.confidence)}`} title="Confiance de l’agent">Confiance : {run.confidence}</span>
          ) : null}
          <span className="badge">{t(`actions.${badgeKey}`)}</span>
        </div>
      </div>

      {run.review_reason ? <p className="review-reason"><strong>Pourquoi une validation ?</strong> {run.review_reason}</p> : null}

      <ActionArgsEditor run={run} editedFields={editedFields} onFieldChange={onFieldChange} />

      {summary !== null && <div className="ai-summary">{summary}</div>}

      {canRedraft && (
        <FeedbackChat
          run={run}
          messages={feedbackMessages}
          draft={feedbackDraft}
          onDraftChange={onFeedbackDraftChange}
          onSubmit={(feedback) => onDecision('respond', { feedback })}
          busy={busy}
        />
      )}

      <div className="actions ai-actions">
        <button className="ghost" type="button" onClick={handleSummarize} disabled={summarizing}>
          <span className="material-symbols-outlined" aria-hidden="true">summarize</span><span>Résumer le fil</span>
        </button>
        {toneField ? (
          <span className="tone-group" role="group" aria-label="Ajuster le ton">
            {TONES.map((tone) => (
              <button key={tone.value} className="ghost" type="button" disabled={busy} onClick={() => onDecision('tone', { tone: tone.value, field: toneField })}>
                {tone.label}
              </button>
            ))}
          </span>
        ) : null}
      </div>

      {canApprove && (
        <div className="actions">
          <button className="primary" type="button" disabled={busy} onClick={() => onDecision('accept')}>
            <span className="material-symbols-outlined" aria-hidden="true">send</span><span>Approuver et envoyer</span>
          </button>
          <button className="ghost" type="button" disabled={busy} onClick={() => onDecision('claim')}>
            <span className="material-symbols-outlined" aria-hidden="true">pan_tool</span><span>Prendre en charge</span>
          </button>
          <button className="ghost" type="button" disabled={busy} onClick={() => onDecision('assign')}>
            <span className="material-symbols-outlined" aria-hidden="true">person_add</span><span>Assigner…</span>
          </button>
          <button className="danger" type="button" disabled={busy} onClick={() => onDecision('ignore')}>
            <span className="material-symbols-outlined" aria-hidden="true">block</span><span>Ignorer</span>
          </button>
        </div>
      )}
    </article>
  );
}
