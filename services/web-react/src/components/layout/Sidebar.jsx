import { NavLink } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance } from '../../contexts/InstanceContext';
import { useI18n } from '../../contexts/I18nContext';

const NAV_ITEMS = [
  { to: '/', end: true, icon: 'hub', key: 'nav.instances' },
  { to: '/agent-types', icon: 'deployed_code', key: 'nav.agentTypes' },
  { to: '/health', icon: 'monitor_heart', key: 'nav.health' },
  { to: '/audit', icon: 'receipt_long', key: 'nav.audit' },
  { to: '/users', icon: 'manage_accounts', key: 'nav.users' },
  { to: '/account', icon: 'account_circle', key: 'nav.account' },
];

export default function Sidebar() {
  const { token } = useAuth();
  const { instanceId, currentInstance } = useInstance();
  const { t } = useI18n();
  const signedIn = Boolean(token);
  const instanceLabel = currentInstance?.display_name || instanceId || 'Aucune instance sélectionnée';
  const sessionText = signedIn ? `Espace ${instanceLabel}` : 'Connectez-vous pour contrôler la plateforme';

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">B</div>
        <div>
          <strong>Agora AI</strong>
          <span>Panneau de contrôle</span>
        </div>
      </div>

      <nav className="nav" aria-label="Navigation principale">
        {NAV_ITEMS.map((item) => (
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
            <strong id="agent-connection">{signedIn ? 'Passerelle connectée' : 'Passerelle inactive'}</strong>
            <span id="agent-session">{sessionText}</span>
            <button className="ghost sidebar-action" type="button" hidden>Reconnecter</button>
            <button className="ghost sidebar-action" type="button" hidden></button>
          </div>
        </div>
      </div>
    </aside>
  );
}
