import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { gatewayUrl } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import AgoraLogo from '../components/brand/AgoraLogo';

function readableError(response, text) {
  if (!text) return `${response.status} ${response.statusText || 'Erreur'}`.trim();
  try {
    const parsed = JSON.parse(text);
    return parsed.error || parsed.detail || parsed.message || text;
  } catch {
    return text.replace(/\s+/g, ' ').trim();
  }
}

function passwordRules(password, confirm) {
  return [
    { id: 'length', ok: password.length >= 12, label: 'Au moins 12 caractères' },
    { id: 'letter', ok: /[A-Za-zÀ-ÿ]/.test(password), label: 'Une lettre' },
    { id: 'number', ok: /\d/.test(password), label: 'Un chiffre' },
    { id: 'same', ok: Boolean(confirm) && password === confirm, label: 'Confirmation identique' },
  ];
}

export default function InviteSetupPage() {
  const { token = '' } = useParams();
  const { gatewayBase, signOut } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState('loading');
  const [summary, setSummary] = useState('Vérification du lien d’invitation...');
  const [expiresAt, setExpiresAt] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const rules = passwordRules(password, confirm);
  const formReady = rules.every((rule) => rule.ok);

  useEffect(() => {
    let active = true;
    async function checkInvite() {
      try {
        const response = await fetch(gatewayUrl(gatewayBase, `/auth/invite/${encodeURIComponent(token)}`), {
          credentials: 'include',
        });
        const text = await response.text();
        if (!response.ok) throw new Error(readableError(response, text));
        const data = text ? JSON.parse(text) : {};
        if (!active) return;
        if (!data.valid) {
          setStatus('invalid');
          setSummary('Ce lien n’est plus valide.');
          setMessage('Demandez à votre administrateur de renvoyer une invitation.');
          return;
        }
        setStatus('valid');
        setSummary(`Invitation pour ${data.username || 'votre compte'}`);
        setExpiresAt(data.expiresAt || '');
      } catch (error) {
        if (!active) return;
        setStatus('invalid');
        setSummary('Impossible de vérifier le lien.');
        setMessage(error.message);
      }
    }
    checkInvite();
    return () => { active = false; };
  }, [gatewayBase, token]);

  const submit = async (event) => {
    event.preventDefault();
    if (!formReady) {
      setMessage('Le mot de passe ne respecte pas encore toutes les règles.');
      return;
    }
    setSubmitting(true);
    setMessage('');
    try {
      const response = await fetch(gatewayUrl(gatewayBase, `/auth/invite/${encodeURIComponent(token)}`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const text = await response.text();
      if (!response.ok) throw new Error(readableError(response, text));
      setStatus('done');
      setSummary('Votre accès Agora AI est activé.');
      // Whoever was already signed in on this browser is not the person who
      // just claimed the invitation. Without clearing that session the sign-in
      // page bounces straight back to the previous account's workspace.
      signOut();
      window.setTimeout(() => navigate('/login', { replace: true }), 1800);
    } catch (error) {
      setMessage(error.message || 'Activation impossible.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="invite-page">
      <section className="invite-card" aria-labelledby="invite-title">
        <AgoraLogo />
        <div className="invite-heading">
          <span className="eyebrow">Invitation sécurisée</span>
          <h1 id="invite-title">Choisir votre mot de passe</h1>
          <p>{summary}</p>
          {expiresAt ? <small>Lien valide jusqu’au {new Date(expiresAt).toLocaleString('fr-FR')}.</small> : null}
        </div>

        {status === 'loading' ? (
          <div className="invite-state">
            <span className="material-symbols-outlined spin" aria-hidden="true">progress_activity</span>
            <strong>Vérification en cours</strong>
          </div>
        ) : null}

        {status === 'invalid' ? (
          <div className="invite-state error">
            <strong>{message}</strong>
            <Link to="/login">Retour à la connexion</Link>
          </div>
        ) : null}

        {status === 'valid' ? (
          <form className="login-form invite-form" onSubmit={submit}>
            <label>
              <span>Nouveau mot de passe</span>
              <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </label>
            <label>
              <span>Confirmation</span>
              <input type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} />
            </label>
            <ul className="password-rule-list">
              {rules.map((rule) => (
                <li key={rule.id} className={rule.ok ? 'ok' : ''}>
                  <span className="material-symbols-outlined" aria-hidden="true">{rule.ok ? 'check_circle' : 'radio_button_unchecked'}</span>
                  {rule.label}
                </li>
              ))}
            </ul>
            {message ? <p className="form-error">{message}</p> : null}
            <button className="primary" type="submit" disabled={!formReady || submitting}>
              <span className="material-symbols-outlined" aria-hidden="true">key</span>
              <span>{submitting ? 'Activation...' : 'Activer mon compte'}</span>
            </button>
          </form>
        ) : null}

        {status === 'done' ? (
          <div className="invite-state ok">
            <span className="material-symbols-outlined" aria-hidden="true">verified</span>
            <strong>Redirection vers la connexion...</strong>
          </div>
        ) : null}
      </section>
    </main>
  );
}
