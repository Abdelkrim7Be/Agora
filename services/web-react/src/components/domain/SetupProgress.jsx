const STEP_LABELS = {
  verify_provider: 'Vérification de la connexion',
  fetch_recent: 'Récupération des messages récents',
  import_contacts: 'Import des contacts',
  learn_style: 'Apprentissage du style d’écriture',
  suggest_persona: 'Suggestion de persona',
  detect_signature: 'Détection de signature',
  seed_categories: 'Initialisation des catégories',
  triage_backlog: 'Traitement des messages en attente',
  finalize: 'Finalisation',
};

const STATUS_ICON = {
  pending: 'radio_button_unchecked',
  running: 'progress_activity',
  done: 'check_circle',
  skipped: 'remove_circle',
  failed: 'error',
  abandoned: 'block',
};

const STATUS_CLASS = {
  pending: '',
  running: 'warn',
  done: 'ok',
  skipped: '',
  failed: 'error',
  abandoned: 'error',
};

function formatElapsed(startedAt) {
  if (!startedAt) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes} min ${rest} s`;
}

function detailSummary(step) {
  if (!step.detail) return null;
  const d = step.detail;
  if (d.reason) return d.reason;
  if (typeof d.contacts_imported === 'number') return `${d.contacts_imported} contacts importés`;
  if (typeof d.sample_count === 'number') return `${d.sample_count} e-mails analysés`;
  if (typeof d.messages_fetched === 'number') return `${d.messages_fetched} messages récupérés`;
  if (typeof d.categories_seeded === 'number') return `${d.categories_seeded} catégorie(s) initialisée(s)`;
  if (typeof d.backlog_enqueued === 'number') return `${d.backlog_enqueued} message(s) en file`;
  if (d.detected === true) return 'Signature détectée';
  if (d.detected === false) return 'Aucune signature détectée';
  return null;
}

export function SetupProgress({ setup, onRetryStep, canManage }) {
  const steps = setup.steps || [];
  return (
    <div className="setup-progress">
      <div className="setup-progress-bar">
        <div className="setup-progress-bar-fill" style={{ width: `${setup.progress?.percent ?? 0}%` }} />
      </div>
      <ul className="setup-step-list">
        {steps.map((step) => (
          <li key={step.step_key} className={`setup-step status-${step.status}`}>
            <span className={`material-symbols-outlined setup-step-icon ${STATUS_CLASS[step.status] || ''}`} aria-hidden="true">
              {STATUS_ICON[step.status] || 'radio_button_unchecked'}
            </span>
            <div className="setup-step-body">
              <strong>{STEP_LABELS[step.step_key] || step.step_key}</strong>
              {step.status === 'running' && step.started_at ? (
                <span className="setup-step-detail">En cours depuis {formatElapsed(step.started_at)}</span>
              ) : null}
              {detailSummary(step) ? <span className="setup-step-detail">{detailSummary(step)}</span> : null}
              {step.status === 'failed' && step.error ? <span className="setup-step-error">{step.error}</span> : null}
            </div>
            {step.status === 'failed' && canManage && onRetryStep ? (
              <button type="button" className="ghost" onClick={() => onRetryStep(step.step_key)}>Relancer</button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
