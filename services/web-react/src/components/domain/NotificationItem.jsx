const SEVERITY_LABEL = {
  info: 'Info',
  success: 'Succès',
  warning: 'Attention',
  error: 'Erreur',
};

export function NotificationItem({ notification, onMarkRead, onDelete, onNavigate }) {
  const unread = !notification.read_at;
  return (
    <li className={`notification-item severity-${notification.severity}${unread ? ' unread' : ''}`}>
      <span className={`status-pill ${notification.severity === 'error' ? 'error' : notification.severity === 'warning' ? 'warn' : 'ok'}`}>
        {SEVERITY_LABEL[notification.severity] || notification.severity}
      </span>
      <div className="notification-body">
        <strong
          className={notification.action_url ? 'notification-title clickable' : 'notification-title'}
          onClick={notification.action_url ? () => onNavigate?.(notification) : undefined}
        >
          {notification.title}
          {notification.occurrence_count > 1 ? <span className="notification-count"> ×{notification.occurrence_count}</span> : null}
        </strong>
        {notification.body ? <p>{notification.body}</p> : null}
        <span className="notification-time">{notification.created_at}</span>
      </div>
      <div className="notification-actions">
        {unread && onMarkRead ? (
          <button type="button" className="ghost" onClick={() => onMarkRead(notification.id)}>Marquer comme lu</button>
        ) : null}
        {onDelete ? (
          <button type="button" className="ghost" onClick={() => onDelete(notification.id)}>Supprimer</button>
        ) : null}
      </div>
    </li>
  );
}
