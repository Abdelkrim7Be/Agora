import { useState } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance } from '../../contexts/InstanceContext';
import { useTheme } from '../../contexts/ThemeContext';
import { useStatus } from '../../contexts/StatusContext';
import { gatewayUrl } from '../../api/client';
import { useUnreadCountQuery, useNotificationsQuery, useMarkNotificationRead, useSubmitReport } from '../../api/queries';
import { roleLabelFr } from '../../utils/format';
import { currentUsername } from '../../utils/jwt';

const EMPTY_REPORT = { subject: '', description: '', severity: 'medium' };

function ReportIssueButton() {
  const { instanceId } = useParams();
  const location = useLocation();
  const { setStatus } = useStatus();
  const submitReport = useSubmitReport();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_REPORT);

  const submit = async () => {
    if (!form.subject.trim() || !form.description.trim()) {
      setStatus('Sujet et description requis pour signaler un problème.', 'error');
      return;
    }
    try {
      await submitReport.mutateAsync({
        subject: form.subject.trim(),
        description: form.description.trim(),
        severity: form.severity,
        contextInstanceId: instanceId || null,
        contextPage: location.pathname,
      });
      setStatus('Signalement envoyé. Un administrateur va le consulter.', 'ok');
      setForm(EMPTY_REPORT);
      setOpen(false);
    } catch (error) {
      setStatus(`Impossible d’envoyer le signalement : ${error.message}`, 'error');
    }
  };

  return (
    <div className="report-popover-wrap">
      <button
        type="button"
        className="icon-button"
        aria-label="Signaler un problème"
        title="Signaler un problème"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="material-symbols-outlined" aria-hidden="true">flag</span>
      </button>
      {open ? (
        <div className="report-popover" role="dialog" aria-label="Signaler un problème">
          <div className="notification-popover-header">
            <strong>Signaler un problème</strong>
            <button type="button" className="ghost icon-button" aria-label="Fermer" onClick={() => setOpen(false)}>
              <span className="material-symbols-outlined" aria-hidden="true">close</span>
            </button>
          </div>
          <div className="report-popover-body">
            <label>Sujet
              <input
                value={form.subject}
                onChange={(event) => setForm({ ...form, subject: event.target.value })}
                placeholder="Le brouillon ne s’envoie pas"
                autoFocus
              />
            </label>
            <label>Description
              <textarea
                rows={4}
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                placeholder="Ce que vous avez fait, ce qui s’est passé"
              />
            </label>
            <label>Gravité
              <select value={form.severity} onChange={(event) => setForm({ ...form, severity: event.target.value })}>
                <option value="low">Faible</option>
                <option value="medium">Moyenne</option>
                <option value="high">Élevée</option>
              </select>
            </label>
            <button className="primary" type="button" disabled={submitReport.isPending} onClick={submit}>
              <span className="material-symbols-outlined" aria-hidden="true">send</span>
              <span>{submitReport.isPending ? 'Envoi…' : 'Envoyer'}</span>
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NotificationBell() {
  const { instanceId } = useParams();
  if (!instanceId) return null;
  return <InstanceNotificationBell instanceId={instanceId} />;
}

function InstanceNotificationBell({ instanceId }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const unreadQuery = useUnreadCountQuery();
  const notificationsQuery = useNotificationsQuery(false);
  const markRead = useMarkNotificationRead();
  const unreadCount = unreadQuery.data?.unread_count ?? 0;
  const newest = (notificationsQuery.data?.notifications || []).slice(0, 10);
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
  const { currentInstance, currentInstanceRole } = useInstance();
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
  // The chip identifies the signed-in person; the instance's mailbox is only a
  // fallback for tokens that carry no subject.
  const displayName = currentUsername(token)
    || currentInstance?.assigned_to
    || currentInstance?.mailbox_identity
    || 'Utilisateur connecté';
  const initials = String(displayName).split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'U';
  const roleLabel = roleLabelFr(currentInstanceRole);

  return (
    <header className="topbar">
      <div className="search-box">
        <span className="material-symbols-outlined" aria-hidden="true">search</span>
        <input aria-label="Rechercher dans la vue" placeholder="Rechercher dans la vue" />
      </div>
      <div className="auth-actions" aria-label="Contrôles de session">
        {signedIn ? <ReportIssueButton /> : null}
        {signedIn ? <NotificationBell /> : null}
        {signedIn ? (
          <div className="identity-chip" title={`${displayName} · connecté`}>
            <span className="identity-avatar">{initials}</span>
            <span className="identity-copy">
              <strong>{displayName}</strong>
              <small>{roleLabel} · connecté</small>
            </span>
          </div>
        ) : null}
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
