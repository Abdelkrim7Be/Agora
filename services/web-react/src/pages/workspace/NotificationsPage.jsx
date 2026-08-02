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
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [browserPermission, setBrowserPermission] = useState(() => window.Notification?.permission || 'unsupported');

  const query = useNotificationsQuery(unreadOnly);
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const deleteNotification = useDeleteNotification();

  const notifications = (query.data?.notifications || []).filter(
    (n) => !severityFilter || n.severity === severityFilter
  );
  const allSelected = notifications.length > 0 && notifications.every((n) => selectedIds.has(n.id));
  const selectedNotifications = notifications.filter((n) => selectedIds.has(n.id));

  const toggleAll = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allSelected) notifications.forEach((n) => next.delete(n.id));
      else notifications.forEach((n) => next.add(n.id));
      return next;
    });
  };

  const toggleOne = (id) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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

  const handleBulkRead = async () => {
    try {
      const unread = selectedNotifications.filter((n) => !n.read_at);
      await Promise.all(unread.map((n) => markRead.mutateAsync(n.id)));
      setSelectedIds(new Set());
      setStatus(`${unread.length} notification(s) marquée(s) comme lues.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de marquer la sélection : ${error.message}`, 'error');
    }
  };

  const handleBulkDelete = async () => {
    try {
      await Promise.all(selectedNotifications.map((n) => deleteNotification.mutateAsync(n.id)));
      setSelectedIds(new Set());
      setStatus(`${selectedNotifications.length} notification(s) supprimée(s).`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer la sélection : ${error.message}`, 'error');
    }
  };

  const handleNavigate = (notification) => {
    if (notification.action_url) navigate(notification.action_url);
  };

  const handleBrowserPermission = async () => {
    if (!window.Notification) {
      setStatus('Les notifications du navigateur ne sont pas prises en charge.', 'error');
      return;
    }
    const permission = await Notification.requestPermission();
    setBrowserPermission(permission);
    setStatus(permission === 'granted' ? 'Notifications du navigateur activées.' : 'Notifications du navigateur non activées.', permission === 'granted' ? 'ok' : 'error');
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
            <button type="button" disabled={!notifications.length} onClick={toggleAll}>{allSelected ? 'Tout désélectionner' : 'Tout sélectionner'}</button>
            <button type="button" disabled={!selectedNotifications.length} onClick={handleBulkRead}>Marquer la sélection comme lue</button>
            <button type="button" className="danger" disabled={!selectedNotifications.length} onClick={handleBulkDelete}>Supprimer la sélection</button>
            <button type="button" disabled={browserPermission === 'granted' || browserPermission === 'unsupported'} onClick={handleBrowserPermission}>
              Notifications navigateur
            </button>
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
              <li className="notification-select-row" key={notification.id}>
                <input
                  type="checkbox"
                  aria-label={`Sélectionner ${notification.title}`}
                  checked={selectedIds.has(notification.id)}
                  onChange={() => toggleOne(notification.id)}
                />
                <NotificationItem
                  notification={notification}
                  onMarkRead={handleMarkRead}
                  onDelete={handleDelete}
                  onNavigate={handleNavigate}
                />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
