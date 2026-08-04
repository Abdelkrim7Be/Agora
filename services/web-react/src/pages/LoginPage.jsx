import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useApi } from '../api/useApi';
import AgoraLogo from '../components/brand/AgoraLogo';

export default function LoginPage() {
  const { login } = useAuth();
  const { api } = useApi();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState({ message: '', kind: '' });
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setStatus({ message: '', kind: '' });
    try {
      const result = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: username.trim(), password }),
      });
      login(result.token);
      navigate('/');
    } catch (error) {
      setStatus({ message: `Échec de connexion : ${error.message}`, kind: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div id="login-modal" className="modal">
      <section className="modal-panel login-panel" role="dialog" aria-modal="true" aria-labelledby="login-title">
        <div className="modal-header">
          <div>
            <AgoraLogo className="login-brand" />
            <h2 id="login-title">Bienvenue dans votre espace Agora AI</h2>
            <p>Supervisez vos agents métiers, vos validations et vos boîtes connectées depuis un environnement sécurisé.</p>
          </div>
        </div>
        <p className={`login-status${status.kind ? ` ${status.kind}` : ''}`} role="status">
          {status.message}
        </p>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>Nom d'utilisateur</span>
            <input
              aria-label="Nom d'utilisateur"
              placeholder="Nom d'utilisateur"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            <span>Mot de passe</span>
            <input
              aria-label="Mot de passe"
              type="password"
              placeholder="Mot de passe"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <button className="primary" type="submit" disabled={submitting}>
            <span className="material-symbols-outlined" aria-hidden="true">login</span>
            <span>Accéder à la plateforme</span>
          </button>
        </form>
        <div className="login-trust-row" aria-label="Garanties de sécurité">
          <span>Validation humaine</span>
          <span>Audit activé</span>
          <span>Accès sécurisé</span>
        </div>
      </section>
      <aside className="login-side" aria-hidden="true">
        <div className="login-product-panel">
          <span className="eyebrow">Agora Consulting · agents métiers supervisés</span>
          <h2>Des agents IA utiles, mais jamais hors contrôle.</h2>
          <p>Centralisez les validations, la synchronisation des boîtes mail, les règles métier et les journaux d’audit dans une interface pensée pour les équipes clientes.</p>
          <div className="login-proof-grid">
            <div><strong>Supervision</strong><span>Chaque action sensible passe par une validation claire.</span></div>
            <div><strong>Traçabilité</strong><span>Les accès et décisions restent consultables.</span></div>
            <div><strong>Connecteurs</strong><span>Boîtes mail, cas métier et routage dans un seul espace.</span></div>
          </div>
        </div>
      </aside>
    </div>
  );
}
