import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TablePager } from '../../components/ui/TablePager';
import { usePagination } from '../../hooks/usePagination';
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
  const pager = usePagination(notifications);
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
          {/* Filters and single actions only. The four selection buttons used to
              sit here too, greyed out most of the time — they now appear as a bar
              when there is actually a selection to act on. */}
          <div className="toolbar notifications-toolbar">
            <label className="toggle-row">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
              <span>Non lues uniquement</span>
            </label>
            <select aria-label="Filtre de sévérité" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
              <option value="">Toutes sévérités</option>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" onClick={handleMarkAllRead}>
              <span className="material-symbols-outlined" aria-hidden="true">mark_email_read</span>
              <span>Tout marquer comme lu</span>
            </button>
            <button
              className="ghost"
              type="button"
              disabled={browserPermission === 'granted' || browserPermission === 'unsupported'}
              title={browserPermission === 'granted' ? 'Déjà autorisées dans ce navigateur' : undefined}
              onClick={handleBrowserPermission}
            >
              <span className="material-symbols-outlined" aria-hidden="true">notifications_active</span>
              <span>Alertes navigateur</span>
            </button>
            <button className="ghost" type="button" aria-label="Actualiser" onClick={() => query.refetch()}>
              <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
            </button>
          </div>
        </div>
        {notifications.length ? (
          <div className="bulk-bar notifications-bulk-bar">
            <button type="button" onClick={toggleAll}>
              {allSelected ? 'Tout désélectionner' : 'Tout sélectionner'}
            </button>
            {selectedNotifications.length ? (
              <>
                <span>{selectedNotifications.length} sélectionnée(s)</span>
                <button type="button" onClick={handleBulkRead}>Marquer comme lues</button>
                <button className="danger" type="button" onClick={handleBulkDelete}>Supprimer</button>
              </>
            ) : <span>Sélectionnez des notifications pour agir en lot.</span>}
          </div>
        ) : null}
        {query.error ? (
          <p className="empty-cell">{`Notifications indisponibles : ${query.error.message}`}</p>
        ) : !notifications.length ? (
          <p className="empty-cell">Aucune notification.</p>
        ) : (
          <ul className="notification-list">
            {pager.visible.map((notification) => (
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
        {notifications.length ? (
          <TablePager
            page={pager.page}
            pageCount={pager.pageCount}
            total={pager.total}
            size={pager.size}
            onPage={pager.setPage}
            onSize={pager.setSize}
            unit="notifications"
          />
        ) : null}
      </Card>
    </>
  );
}
