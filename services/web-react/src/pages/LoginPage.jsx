import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useApi } from '../api/useApi';

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
            <div className="login-brand"><span className="brand-mark">B</span><strong>Agora AI</strong></div>
            <h2 id="login-title">Bon retour</h2>
            <p>Connectez-vous pour accéder à la plateforme d'agents IA.</p>
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
      </section>
      <aside className="login-side" aria-hidden="true">
        <div>
          <span className="eyebrow">Agora Consulting · Plateforme d'agents IA</span>
          <h2>Pilotez les assistants IA de Agora Consulting depuis un seul endroit.</h2>
          <p>Approbations, activité, accès, politiques et coûts restent derrière l'espace connecté.</p>
        </div>
      </aside>
    </div>
  );
}
