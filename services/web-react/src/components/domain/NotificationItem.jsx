import { formatDateTimeFr } from '../../utils/format';

const SEVERITY = {
  info: { label: 'Info', icon: 'info', pill: '' },
  success: { label: 'Succès', icon: 'check_circle', pill: 'ok' },
  warning: { label: 'Attention', icon: 'warning', pill: 'warn' },
  error: { label: 'Erreur', icon: 'error', pill: 'error' },
};

export function NotificationItem({ notification, onMarkRead, onDelete, onNavigate }) {
  const unread = !notification.read_at;
  const severity = SEVERITY[notification.severity] || SEVERITY.info;
  return (
    <li className={`notification-item severity-${notification.severity}${unread ? ' unread' : ''}`}>
      <span className={`notification-severity-icon ${severity.pill}`} aria-hidden="true">
        <span className="material-symbols-outlined">{severity.icon}</span>
      </span>
      <div className="notification-body">
        <div className="notification-heading">
          <strong
            className={notification.action_url ? 'notification-title clickable' : 'notification-title'}
            onClick={notification.action_url ? () => onNavigate?.(notification) : undefined}
          >
            {notification.title}
          </strong>
          {notification.occurrence_count > 1 ? <span className="notification-count">×{notification.occurrence_count}</span> : null}
          {unread ? <span className="notification-dot" aria-hidden="true" /> : null}
        </div>
        {notification.body ? <p>{notification.body}</p> : null}
        <span className="notification-time">{formatDateTimeFr(notification.created_at)}</span>
      </div>
      <div className="notification-actions">
        {unread && onMarkRead ? (
          <button type="button" className="ghost icon-button" title="Marquer comme lu" aria-label="Marquer comme lu" onClick={() => onMarkRead(notification.id)}>
            <span className="material-symbols-outlined" aria-hidden="true">mark_email_read</span>
          </button>
        ) : null}
        {onDelete ? (
          <button type="button" className="ghost icon-button" title="Supprimer" aria-label="Supprimer" onClick={() => onDelete(notification.id)}>
            <span className="material-symbols-outlined" aria-hidden="true">delete</span>
          </button>
        ) : null}
      </div>
    </li>
  );
}
