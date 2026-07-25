import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useTheme } from '../../contexts/ThemeContext';
import { gatewayUrl } from '../../api/client';
import { useUnreadCountQuery, useNotificationsQuery, useMarkNotificationRead } from '../../api/queries';

function NotificationBell() {
  const { instanceId } = useParams();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const unreadQuery = useUnreadCountQuery();
  const notificationsQuery = useNotificationsQuery(false);
  const markRead = useMarkNotificationRead();
  const unreadCount = unreadQuery.data?.unread_count ?? 0;
  const newest = (notificationsQuery.data?.notifications || []).slice(0, 10);

  if (!instanceId) return null;

  return (
    <div className="notification-bell-wrap">
      <button
        type="button"
        className="icon-button notification-bell"
        aria-label="Notifications"
        title="Notifications"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="material-symbols-outlined" aria-hidden="true">notifications</span>
        {unreadCount > 0 ? <span className="notification-badge">{unreadCount > 99 ? '99+' : unreadCount}</span> : null}
      </button>
      {open ? (
        <div className="notification-popover">
          <div className="notification-popover-header">
            <strong>Notifications</strong>
            <button type="button" className="ghost" onClick={() => { setOpen(false); navigate(`/instance/${instanceId}/notifications`); }}>
              Voir tout
            </button>
          </div>
          {!newest.length ? (
            <p className="empty-cell">Aucune notification.</p>
          ) : (
            <ul className="notification-popover-list">
              {newest.map((n) => (
                <li key={n.id} className={n.read_at ? '' : 'unread'} onClick={() => { markRead.mutate(n.id); if (n.action_url) { setOpen(false); navigate(n.action_url); } }}>
                  <strong>{n.title}</strong>
                  <span>{n.created_at}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

export default function Topbar() {
  const { token, gatewayBase, signOut } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const signedIn = Boolean(token);

  const handleSignOut = async () => {
    try {
      if (token) {
        await fetch(gatewayUrl(gatewayBase, '/auth/logout'), {
          method: 'POST',
          credentials: 'include',
          headers: { Authorization: `Bearer ${token}` },
        });
      }
    } catch (_error) {
      // Local sign-out must still complete if the gateway is unreachable.
    }
    signOut();
    navigate('/login');
  };

  return (
    <header className="topbar">
      <div className="search-box">
        <span className="material-symbols-outlined" aria-hidden="true">search</span>
        <input aria-label="Rechercher dans la vue" placeholder="Rechercher dans la vue" />
      </div>
      <div className="auth-actions" aria-label="Contrôles de session">
        {signedIn ? <NotificationBell /> : null}
        <button
          className="icon-button theme-toggle"
          type="button"
          aria-label={theme === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'}
          title={theme === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'}
          onClick={toggleTheme}
        >
          <span className="material-symbols-outlined" aria-hidden="true">
            {theme === 'dark' ? 'light_mode' : 'dark_mode'}
          </span>
        </button>
        {signedIn ? (
          <button className="ghost" type="button" onClick={handleSignOut}>
            <span className="material-symbols-outlined" aria-hidden="true">logout</span>
            <span>Se déconnecter</span>
          </button>
        ) : (
          <button type="button" onClick={() => navigate('/login')}>
            <span className="material-symbols-outlined" aria-hidden="true">login</span>
            <span>Se connecter</span>
          </button>
        )}
      </div>
    </header>
  );
}
