import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useTheme } from '../../contexts/ThemeContext';
import { gatewayUrl } from '../../api/client';

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
