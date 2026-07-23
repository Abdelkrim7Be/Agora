import { useState } from 'react';
import { useInstance } from '../../contexts/InstanceContext';
import { useI18n } from '../../contexts/I18nContext';
import { useSummarizeRun } from '../../api/queries';
import {
  actionRequest,
  actionArgs,
  redraftCapable,
  formatDateTimeFr,
  formatDurationFr,
  confidenceClass,
  actionArgLabel,
  formatEditableValue,
  coerceEditedValue,
  ACTION_ARG_HIDDEN,
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

  const request = actionRequest(run);
  const args = actionArgs(run);
  const fields = Object.entries(args).filter(([key]) => !ACTION_ARG_HIDDEN.has(key));
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

  const handleFeedbackSubmit = (event) => {
    event.preventDefault();
    if (!feedbackDraft.trim()) return;
    onDecision('respond', { feedback: feedbackDraft });
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

      <div className="action-preview">
        {request.action === 'forward_email' ? (
          <div className="route-preview">
            <strong>Transférer cet e-mail à {args.to || 'destinataire'}</strong>
            <span>{run.workflow_owner ? `Propriétaire : ${run.workflow_owner}` : 'Routage du workflow'}</span>
          </div>
        ) : null}
        {fields.length ? fields.map(([key, value]) => (
          <label className="action-row" key={key}>
            <strong>{actionArgLabel(key)}</strong>
            <textarea
              value={editedFields[key] !== undefined ? editedFields[key] : formatEditableValue(value)}
              onChange={(event) => onFieldChange(run.run_id, key, coerceEditedValue(value, event.target.value))}
            />
          </label>
        )) : <div className="empty">Aucun argument modifiable pour cette action.</div>}
      </div>

      {summary !== null && <div className="ai-summary">{summary}</div>}

      {canRedraft && (
        <form className="feedback-chat" aria-label="Fil de retouche du brouillon" onSubmit={handleFeedbackSubmit}>
          <div className="feedback-chat-header">
            <span className="material-symbols-outlined" aria-hidden="true">forum</span>
            <div>
              <strong>Retouches du brouillon</strong>
              <span>Décrivez le changement attendu, l’agent mettra à jour le brouillon dans cette carte.</span>
            </div>
          </div>
          <div className="feedback-log" aria-live="polite">
            {feedbackMessages.length ? feedbackMessages.map((message) => (
              <div key={message.id} className={`feedback-message ${message.role === 'user' ? 'user' : 'agent'}${message.live ? ' live' : ''}`} aria-busy={message.live || undefined}>
                <strong>{message.role === 'user' ? 'Vous' : 'Agent'}{message.at ? <time> {formatDateTimeFr(message.at)}</time> : null}</strong>
                <span>{message.content}</span>
                {message.live ? <span className="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span> : null}
              </div>
            )) : <div className="feedback-empty">Aucun retour envoyé. Écrivez ce que vous voulez modifier dans le brouillon.</div>}
          </div>
          <div className="feedback-compose">
            <textarea
              data-testid={`feedback-input-${run.run_id}`}
              rows={2}
              placeholder="Ex. Rends le ton plus direct et ajoute la signature"
              value={feedbackDraft}
              onChange={(event) => onFeedbackDraftChange(run.run_id, event.target.value)}
            />
            <button className="feedback-send" type="submit" title="Envoyer le retour" disabled={busy}>
              <span className="material-symbols-outlined" aria-hidden="true">send</span>
            </button>
          </div>
        </form>
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
