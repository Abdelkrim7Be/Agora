import { NavLink, useParams } from 'react-router-dom';
import { useInstance } from '../../contexts/InstanceContext';
import { useAuth } from '../../contexts/AuthContext';
import { agentTypeLabel } from '../../utils/format';
import { useAgentTypesQuery, usePendingRunsQuery, useUnreadCountQuery } from '../../api/queries';
import AgoraLogo from '../brand/AgoraLogo';

const TAB_GROUPS = [
  {
    label: 'Travail',
    tabs: [
      { to: 'guide', icon: 'help', label: 'Guide' },
      { to: '', end: true, icon: 'dashboard', label: 'Tableau de bord' },
      { to: 'validation', icon: 'inbox', label: 'À valider' },
      { to: 'inbox', icon: 'mail', label: 'Messages' },
      { to: 'notifications', icon: 'notifications', label: 'Notifications' },
    ],
  },
  {
    label: 'Connecteurs',
    tabs: [
      { to: 'gmail', icon: 'mark_email_read', label: 'Boîte connectée' },
    ],
  },
  {
    label: 'Configuration',
    tabs: [
      { to: 'config', icon: 'person', label: 'Persona' },
      { to: 'style', icon: 'edit_note', label: 'Style de réponse' },
      { to: 'signature', icon: 'draw', label: 'Signature' },
      { to: 'memory', icon: 'psychology', label: 'Mémoire' },
      { to: 'rules', icon: 'rule', label: 'Règles' },
      { to: 'junk', icon: 'filter_alt', label: 'Filtre anti-bruit' },
    ],
  },
  {
    label: 'Gestion',
    tabs: [
      { to: 'categories', icon: 'category', label: 'Cas métier' },
      { to: 'contacts', icon: 'contact_mail', label: 'Contacts' },
      { to: 'segments', icon: 'group_work', label: 'Segments' },
      { to: 'roles', icon: 'groups', label: 'Annuaire des rôles' },
      { to: 'campaigns', icon: 'campaign', label: 'Campagnes', minRole: 'owner' },
    ],
  },
  {
    label: 'Contrôle',
    tabs: [
      { to: 'capabilities', icon: 'shield', label: 'Capacités', minGlobalRole: 'admin' },
      { to: 'permissions', icon: 'admin_panel_settings', label: 'Permissions', minRole: 'owner' },
      { to: 'dlq', icon: 'warning', label: 'File d’erreurs', minGlobalRole: 'admin' },
      { to: 'costs', icon: 'monitoring', label: 'Coûts', minGlobalRole: 'admin' },
    ],
  },
];

// Routes the curated groups above already reach. `config` and `persona` render
// the same page, so a manifest declaring either is considered covered.
const CURATED_ROUTES = new Set(
  TAB_GROUPS.flatMap((group) => group.tabs.map((tab) => tab.to)).concat('persona'),
);

/** Settings sections an agent type declares that this sidebar does not hardcode.
 *
 * The email agent's own sections are all curated above — richer labels, icons and
 * role gates than a manifest can carry — so this adds nothing for it. It is what
 * gives a *second* agent type a working settings navigation without anyone
 * writing frontend for it: the agent declares, the platform renders.
 */
function declaredSections(agentType, types) {
  const type = (types || []).find((candidate) => candidate.id === agentType);
  return (type?.settings_schema || [])
    .filter((section) => typeof section?.path === 'string' && section.path.startsWith('/'))
    .map((section) => ({ ...section, route: section.path.replace(/^\//, '') }))
    .filter((section) => section.route && !CURATED_ROUTES.has(section.route));
}

export default function WorkspaceSidebar() {
  const { globalRole } = useAuth();
  const { instanceId, currentInstance, hasRole } = useInstance();
  const params = useParams();
  const typesQuery = useAgentTypesQuery();
  const pendingQuery = usePendingRunsQuery(0);
  const unreadQuery = useUnreadCountQuery();
  const id = params.instanceId || instanceId;
  const typeLabel = agentTypeLabel(currentInstance?.agent_type, typesQuery.data || []);
  const pendingCount = pendingQuery.data?.runs?.length ?? 0;
  const unreadCount = unreadQuery.data?.unread_count ?? 0;
  const declared = declaredSections(currentInstance?.agent_type, typesQuery.data);

  return (
    <aside className="sidebar">
      <AgoraLogo />

      <div id="workspace-sidebar-context">
        <NavLink to={globalRole === 'admin' ? '/' : '/account'} end className="nav-item workspace-back">
          <span className="material-symbols-outlined" aria-hidden="true">arrow_back</span>
          <span>Plateforme</span>
        </NavLink>
        <div className="workspace-sidebar-agent">
          <span>Espace métier</span>
          <strong>{currentInstance?.display_name || id}</strong>
          <small>{typeLabel}</small>
        </div>
      </div>

      <nav className="workspace-tabs" aria-label="Onglets de l'espace de travail">
        {TAB_GROUPS.map((group) => {
          const tabs = group.tabs.filter((tab) => (!tab.minRole || hasRole(tab.minRole)) && (!tab.minGlobalRole || globalRole === tab.minGlobalRole));
          if (!tabs.length) return null;
          return (
            <div className="tab-group" key={group.label}>
              <span className="tab-group-label">{group.label}</span>
              <div className="tab-group-items">
                {tabs.map((tab) => (
                  <NavLink
                    key={tab.to || 'index'}
                    to={tab.to}
                    end={tab.end}
                    className={({ isActive }) => `workspace-tab nav-item${isActive ? ' active' : ''}`}
                  >
                    <span className="material-symbols-outlined" aria-hidden="true">{tab.icon}</span>
                    <span>{tab.label}</span>
                    {tab.to === 'validation' ? <strong>{pendingCount}</strong> : null}
                    {tab.to === 'notifications' && unreadCount > 0 ? <strong>{unreadCount}</strong> : null}
                  </NavLink>
                ))}
              </div>
            </div>
          );
        })}
        {declared.length ? (
          <div className="tab-group">
            <span className="tab-group-label">Réglages de l'agent</span>
            <div className="tab-group-items">
              {declared.map((section) => (
                <NavLink
                  key={section.key}
                  to={section.route}
                  title={section.description || undefined}
                  className={({ isActive }) => `workspace-tab nav-item${isActive ? ' active' : ''}`}
                >
                  <span className="material-symbols-outlined" aria-hidden="true">tune</span>
                  <span>{section.label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        ) : null}
      </nav>
    </aside>
  );
}
