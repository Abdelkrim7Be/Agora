import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { NotificationItem } from '../../components/domain/NotificationItem';
import {
  useNotificationsQuery,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
  useDeleteNotification,
} from '../../api/queries';

const SEVERITIES = ['info', 'success', 'warning', 'error'];

export default function NotificationsPage() {
  const { instanceId } = useInstance();
  const { setStatus } = useStatus();
  const navigate = useNavigate();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [severityFilter, setSeverityFilter] = useState('');

  const query = useNotificationsQuery(unreadOnly);
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const deleteNotification = useDeleteNotification();

  const notifications = (query.data?.notifications || []).filter(
    (n) => !severityFilter || n.severity === severityFilter
  );

  const handleMarkRead = async (id) => {
    try {
      await markRead.mutateAsync(id);
    } catch (error) {
      setStatus(`Impossible de marquer comme lu : ${error.message}`, 'error');
    }
  };

  const handleMarkAllRead = async () => {
    try {
      const result = await markAllRead.mutateAsync();
      setStatus(`${result.marked_read} notification(s) marquée(s) comme lues.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de tout marquer comme lu : ${error.message}`, 'error');
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteNotification.mutateAsync(id);
    } catch (error) {
      setStatus(`Impossible de supprimer : ${error.message}`, 'error');
    }
  };

  const handleNavigate = (notification) => {
    if (notification.action_url) navigate(notification.action_url);
  };

  return (
    <>
      <PageHeading view="notifications" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Notifications</h2>
            <div className="meta"><span>{instanceId}</span></div>
          </div>
          <div className="toolbar">
            <label className="toggle-row">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
              <span>Non lues uniquement</span>
            </label>
            <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
              <option value="">Toutes sévérités</option>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" onClick={handleMarkAllRead}>Tout marquer comme lu</button>
            <button type="button" onClick={() => query.refetch()}>Actualiser</button>
          </div>
        </div>
        {query.error ? (
          <p className="empty-cell">{`Notifications indisponibles : ${query.error.message}`}</p>
        ) : !notifications.length ? (
          <p className="empty-cell">Aucune notification.</p>
        ) : (
          <ul className="notification-list">
            {notifications.map((notification) => (
              <NotificationItem
                key={notification.id}
                notification={notification}
                onMarkRead={handleMarkRead}
                onDelete={handleDelete}
                onNavigate={handleNavigate}
              />
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
