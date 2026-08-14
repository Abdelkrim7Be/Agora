import { NavLink } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useI18n } from '../../contexts/I18nContext';
import { currentUsername } from '../../utils/jwt';
import { roleAtLeast } from '../../utils/roles';
import AgoraLogo from '../brand/AgoraLogo';

const NAV_ITEMS = [
  { to: '/', end: true, icon: 'dashboard', key: 'nav.platformDashboard', minRole: 'admin' },
  { to: '/instances', icon: 'deployed_code', key: 'nav.instances' },
  { to: '/agent-types', icon: 'smart_toy', key: 'nav.agentTypes' },
  { to: '/system-health', icon: 'monitor_heart', key: 'nav.health', minRole: 'admin' },
  { to: '/platform-audit', icon: 'health_and_safety', key: 'nav.platformAudit', minRole: 'admin' },
  { to: '/report-inbox', icon: 'flag', key: 'nav.reports', minRole: 'admin' },
  { to: '/access-log', icon: 'receipt_long', key: 'nav.audit', minRole: 'admin' },
  { to: '/team', icon: 'group', key: 'nav.users', minRole: 'admin' },
  { to: '/account', icon: 'account_circle', key: 'nav.account' },
];

export default function Sidebar() {
  const { token, globalRole } = useAuth();
  const { t } = useI18n();
  const signedIn = Boolean(token);
  const username = currentUsername(token);
  const sessionText = signedIn ? username || 'Session ouverte' : 'Connexion requise';
  const navItems = NAV_ITEMS.filter((item) => !item.minRole || roleAtLeast(globalRole, item.minRole));

  return (
    <aside className="sidebar">
      <AgoraLogo />

      <nav className="nav" aria-label="Navigation principale">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
          >
            <span className="material-symbols-outlined" aria-hidden="true">{item.icon}</span>
            <span>{t(item.key)}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="agent-card" id="sidebar-status-card" data-testid="sidebar-status">
          <span className="pulse" id="sidebar-status-dot"></span>
          <div>
            <strong id="agent-connection">{signedIn ? 'Accès sécurisé' : 'Session inactive'}</strong>
            <span id="agent-session">{sessionText}</span>
            <button className="ghost sidebar-action" type="button" hidden>Reconnecter</button>
            <button className="ghost sidebar-action" type="button" hidden></button>
          </div>
        </div>
      </div>
    </aside>
  );
}
