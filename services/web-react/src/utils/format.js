export function healthClass(value) {
  if (value === 'healthy' || value === 'active') return 'ok';
  if (value === 'unreachable' || value === 'error' || value === 'inactive') return 'error';
  return 'warn';
}

const STATUS_LABELS_FR = {
  active: 'Actif',
  inactive: 'Inactif',
  healthy: 'Sain',
  unknown: 'Inconnu',
  configured: 'Configurée',
  connected: 'Connectée',
  disconnected: 'Déconnectée',
  unreachable: 'Injoignable',
  error: 'Erreur',
  ok: 'OK',
  running: 'En cours',
  idle: 'En veille',
  pending: 'En attente',
  pending_approval: 'À valider',
  syncing: 'Synchronisation',
  synced: 'Synchronisée',
  disabled: 'Désactivée',
  paused: 'En pause',
  security_hold: 'Contrôle sécurité',
  completed: 'Terminé',
  sent: 'Envoyé',
  ignored: 'Ignoré',
  ignore: 'Ignoré',
  processing: 'En cours',
  dead_letter: 'Échec définitif',
  respond: 'À répondre',
  notify: 'À notifier',
  failed: 'Échec',
  expired: 'Session expirée',
  skipped: 'Ignoré (déjà traité)',
  unclassified: 'Non classé',
};

export function statusLabelFr(value) {
  const raw = String(value || 'unknown').trim();
  const key = raw.toLowerCase();
  if (STATUS_LABELS_FR[key]) return STATUS_LABELS_FR[key];
  const normalized = raw.replace(/[-_]+/g, ' ');
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export const HEALTH_STATUS_LABELS = {
  up: 'Actif',
  down: 'Hors service',
  paused: 'En pause',
  disabled: 'Désactivé',
};

export function healthPillClass(status) {
  if (status === 'up') return 'ok';
  if (status === 'down') return 'error';
  return 'warn'; // paused / disabled / unknown — informational, not a failure
}

export function outcomeClass(outcome, status) {
  const value = `${outcome || ''} ${status || ''}`.toLowerCase();
  if (value.includes('fail') || value.includes('deny') || value.startsWith('5') || value.startsWith('4')) return 'error';
  if (value.includes('warn') || value.includes('pending')) return 'warn';
  return 'ok';
}

export function formatCostEur(value) {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount)) return '€0.00';
  const fractionDigits = amount > 0 && amount < 1 ? 4 : 2;
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(amount);
}

export function formatCountFr(value, digits = 0) {
  return Number(value || 0).toLocaleString('fr-FR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPercentFr(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} %`;
}

export function formatDurationFr(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const totalMinutes = Math.max(0, Math.round(Number(seconds) / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours && minutes) return `${hours} h ${minutes} min`;
  if (hours) return `${hours} h`;
  return `${minutes} min`;
}

export function agentTypeLabel(typeId, agentTypes = []) {
  const raw = String(typeId || 'agent').trim();
  const match = agentTypes.find((type) => type.id === raw);
  if (match?.display_name) return match.display_name;
  if (raw === 'email-agent') return 'Agent e-mail';
  const normalized = raw.replace(/[-_]+/g, ' ');
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export function instanceIdentity(instance) {
  if (instance?.mailbox_identity) return instance.mailbox_identity;
  if (instance?.base_path) return instance.base_path;
  return 'connexion en attente';
}

export function instanceSummaryFields(instance, summary) {
  const health = summary.service_health || instance.status || 'unknown';
  const connection = summary.mailbox_connection || (instance.mailbox_identity ? 'configured' : 'unknown');
  return [
    ['Travail en attente', summary.pending_drafts ?? 0],
    ['Coût du jour', formatCostEur(summary.today_cost_eur ?? 0)],
    ['Connexion', statusLabelFr(connection)],
    ['Exécution', statusLabelFr(summary.sync_status || health)],
  ];
}

export function formatDateTimeFr(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('fr-FR');
}

const CONFIDENCE_CLASS = { 'élevée': 'high', moyenne: 'medium', faible: 'low' };

export function confidenceClass(band) {
  return CONFIDENCE_CLASS[band] || 'medium';
}

export const ACTION_ARG_LABELS_FR = {
  to: 'Destinataire',
  cc: 'Cc',
  bcc: 'Cci',
  subject: 'Objet',
  content: 'Message',
  note: 'Note',
  body: 'Message',
  label: 'Libellé',
  name: 'Nom',
};

export const ACTION_ARG_HIDDEN = new Set(['email_id', 'gmail_thread_id', 'run_id', 'action_id']);

export function actionArgLabel(key) {
  if (ACTION_ARG_LABELS_FR[key]) return ACTION_ARG_LABELS_FR[key];
  const normalized = String(key).replace(/[-_]+/g, ' ');
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export function formatEditableValue(value) {
  if (value && typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value ?? '');
}

export function coerceEditedValue(original, value) {
  if (original && typeof original === 'object') {
    try {
      return JSON.parse(value);
    } catch (_error) {
      return value;
    }
  }
  return value;
}

export function actionRequest(run) {
  return run.pending_action?.[0]?.action_request || {};
}

export function actionArgs(run) {
  return actionRequest(run).args || {};
}

export function redraftCapable(run) {
  const request = actionRequest(run);
  const args = actionArgs(run);
  return ['write_email', 'reply_all', 'create_draft'].includes(request.action) && Object.prototype.hasOwnProperty.call(args, 'content');
}

export function priorityClass(priority) {
  if (priority === 'urgent') return 'error';
  if (priority === 'low') return '';
  return 'warn';
}

export function workflowLabelFr(row) {
  const name = String(row.display_name || '').trim();
  if (name && name !== 'uncategorized') return name;
  const category = String(row.category || '').trim();
  return (!category || category === 'uncategorized') ? 'Sans workflow' : category;
}

// Run Detail's trace table uses plain (non-locale) formatting, distinct from
// the locale-aware formatCountFr/formatCostEur used elsewhere.
export function formatCount(value) {
  return Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function formatCost(value) {
  return `EUR ${Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  })}`;
}

export function summarizeTraceError(error) {
  const raw = String(error || '').trim();
  if (!raw) return '';
  const lower = raw.toLowerCase();
  if (lower.includes('interrupt') || lower.includes('action_request') || lower.includes('pending_action')) {
    return 'Interruption de validation humaine: détails du brouillon masqués.';
  }
  const compact = raw.replace(/\s+/g, ' ');
  return compact.length > 180 ? `${compact.slice(0, 177)}...` : compact;
}

export function friendlySyncError(message) {
  const raw = String(message || '');
  const lower = raw.toLowerCase();
  if (lower.includes('gmail.googleapis.com') || lower.includes('user-rate limit') || lower.includes('ratelimitexceeded') || lower.includes('gmail rate limit')) {
    return 'Limite Gmail atteinte côté Google. La synchronisation est en pause et reprendra automatiquement.';
  }
  if (lower.includes('rate_limit') || lower.includes('rate limit') || lower.includes('429')) {
    return 'AI provider rate limit reached. Wait a few minutes and try again.';
  }
  if (lower.includes('invalid_grant') || lower.includes('expired or revoked') || lower.includes('token has been expired')) {
    return 'L’autorisation Gmail a expiré ou a été révoquée. Reconnectez Gmail.';
  }
  if (lower.includes('could not locate runnable browser') || lower.includes('oauth') || lower.includes('credentials')) {
    return 'Gmail sync is unavailable. Check the Gmail connection settings.';
  }
  return raw && raw.length < 140 && !raw.includes('{') ? raw : 'Gmail sync failed. Check service logs for details.';
}
