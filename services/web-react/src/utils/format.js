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
