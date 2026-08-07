import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance, isInstanceActive } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { currentUsername } from '../../utils/jwt';
import { useBusy } from '../../contexts/BusyContext';
import { useMyProfileQuery, useUpdateMyProfile, useChangeMyPassword } from '../../api/queries';
import { PasswordStrength } from '../../components/ui/PasswordStrength';

function profileStorageKey(username) {
  return `agora.profile.${username || 'anonymous'}`;
}

function loadProfile(username) {
  try {
    return JSON.parse(localStorage.getItem(profileStorageKey(username))) || {};
  } catch (_error) {
    return {};
  }
}

function saveProfile(username, profile) {
  localStorage.setItem(profileStorageKey(username), JSON.stringify(profile));
}

function profileInitials(name) {
  const initials = String(name || '').trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join('');
  return initials.toUpperCase() || '?';
}

function resizeProfilePhoto(file, size) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const side = Math.min(img.width, img.height);
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      canvas.getContext('2d').drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size);
      resolve(canvas.toDataURL('image/jpeg', 0.85));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('image illisible'));
    };
    img.src = url;
  });
}

export default function AccountPage() {
  const { token, gatewayBase, globalRole } = useAuth();
  const { instances, instanceId, currentInstance, currentInstanceRole } = useInstance();
  const { setStatus } = useStatus();
  const username = currentUsername(token) || '—';
  const [profile, setProfileState] = useState(() => loadProfile(username));
  const [displayName, setDisplayName] = useState(profile.displayName || '');
  const [title, setTitle] = useState(profile.title || '');
  const [email, setEmail] = useState('');
  const [passwords, setPasswords] = useState({ current: '', next: '', confirm: '' });
  const [passwordDone, setPasswordDone] = useState(false);
  const fileInputRef = useRef(null);
  const { runBusy } = useBusy();
  const meQuery = useMyProfileQuery();
  const updateMe = useUpdateMyProfile();
  const changePassword = useChangeMyPassword();

  // The display name and e-mail live on the account, not in this browser: they
  // used to be localStorage-only, so they vanished on another machine and
  // nobody else ever saw them.
  useEffect(() => {
    if (!meQuery.data) return;
    setEmail(meQuery.data.email || '');
    if (meQuery.data.displayName) setDisplayName(meQuery.data.displayName);
  }, [meQuery.data]);

  useEffect(() => {
    setStatus('Compte chargé.', 'ok');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const updateProfile = (patch) => {
    const next = { ...loadProfile(username), ...patch };
    saveProfile(username, next);
    setProfileState(next);
  };

  const activeCount = instances.filter(isInstanceActive).length;
  const inactiveCount = instances.length - activeCount;
  const name = profile.displayName || username;

  const handlePhotoChange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const photo = await resizeProfilePhoto(file, 256);
      updateProfile({ photo });
      setStatus('Photo de profil mise à jour.', 'ok');
    } catch (error) {
      setStatus(`Impossible de charger la photo : ${error.message}`, 'error');
    }
  };

  const handlePhotoRemove = () => {
    const next = loadProfile(username);
    delete next.photo;
    saveProfile(username, next);
    setProfileState(next);
    setStatus('Photo de profil retirée.', 'ok');
  };

  const handleSave = async () => {
    // The photo and job title stay local (the account has no column for them);
    // the name and address are the account's own and go to the server.
    updateProfile({ displayName: displayName.trim(), title: title.trim() });
    try {
      await runBusy('Enregistrement du profil', () => updateMe.mutateAsync({
        displayName: displayName.trim(),
        email: email.trim(),
      }));
      setStatus('Profil enregistré.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer le profil : ${error.message}`, 'error');
    }
  };

  const handlePasswordChange = async (event) => {
    event.preventDefault();
    if (passwords.next !== passwords.confirm) {
      setStatus('Les deux nouveaux mots de passe ne correspondent pas.', 'error');
      return;
    }
    if (passwords.next.length < 12) {
      setStatus('Le nouveau mot de passe doit faire au moins 12 caractères.', 'error');
      return;
    }
    setPasswordDone(false);
    try {
      await runBusy('Changement du mot de passe', () => changePassword.mutateAsync({
        currentPassword: passwords.current,
        newPassword: passwords.next,
      }));
      setPasswords({ current: '', next: '', confirm: '' });
      setPasswordDone(true);
      setStatus('Mot de passe modifié. Il servira à votre prochaine connexion.', 'ok');
    } catch (error) {
      setStatus(`Impossible de changer le mot de passe : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="account" />
      <div className="card">
        <div className="profile-hero">
          <div className="profile-avatar">
            {profile.photo ? <img src={profile.photo} alt="Photo de profil" /> : <span>{profileInitials(name)}</span>}
          </div>
          <div className="profile-identity">
            <h2>{name}</h2>
            <p>@{username}{profile.title ? ` · ${profile.title}` : ''}</p>
            <div className="profile-chips">
              <span className="status-pill ok">{globalRole || 'déconnecté'}</span>
              <span className="status-pill warn">{currentInstanceRole} sur {currentInstance?.display_name || instanceId}</span>
            </div>
          </div>
          <div className="profile-actions">
            <input type="file" ref={fileInputRef} accept="image/*" hidden onChange={handlePhotoChange} />
            <button type="button" onClick={() => fileInputRef.current?.click()}>
              <span className="material-symbols-outlined" aria-hidden="true">photo_camera</span>
              <span>Changer la photo</span>
            </button>
            <button type="button" disabled={!profile.photo} onClick={handlePhotoRemove}>Retirer la photo</button>
          </div>
        </div>
        <div className="profile-form">
          <label>Nom affiché
            <input value={displayName} placeholder={username} maxLength={60} onChange={(event) => setDisplayName(event.target.value)} />
          </label>
          <label>Fonction
            <input value={title} placeholder="ex. Responsable RH" maxLength={60} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label>Adresse e-mail
            <input type="email" value={email} placeholder="vous@exemple.com" onChange={(event) => setEmail(event.target.value)} />
          </label>
          <button type="button" className="primary" onClick={handleSave}>Enregistrer le profil</button>
        </div>
        <div className="summary-grid">
          <div><span>Passerelle</span><strong>{gatewayBase || 'même origine'}</strong></div>
          <div><span>Session</span><strong>{token ? 'Authentifié' : 'Déconnecté'}</strong></div>
          <div><span>Rôle global</span><strong>{globalRole || 'déconnecté'}</strong></div>
          <div><span>Rôle sur l’instance</span><strong>{currentInstanceRole}</strong></div>
          <div><span>Instance sélectionnée</span><strong>{currentInstance?.display_name || instanceId}</strong></div>
          <div><span>Type d’agent</span><strong>{currentInstance?.agent_type || 'email-agent'}</strong></div>
          <div><span>Statut de l’instance</span><strong>{String(currentInstance?.status || 'active').toLowerCase()}</strong></div>
          <div><span>Instances visibles</span><strong>{activeCount} actives / {inactiveCount} inactives</strong></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <h2>Mot de passe</h2>
            <div className="meta"><span>Au moins 12 caractères. Vous restez connecté après le changement.</span></div>
          </div>
        </div>
        <form className="profile-form password-form" onSubmit={handlePasswordChange}>
          <label>Mot de passe actuel
            <input
              type="password"
              autoComplete="current-password"
              value={passwords.current}
              onChange={(event) => setPasswords({ ...passwords, current: event.target.value })}
              required
            />
          </label>
          <label>Nouveau mot de passe
            <input
              type="password"
              autoComplete="new-password"
              value={passwords.next}
              onChange={(event) => {
                setPasswordDone(false);
                setPasswords({ ...passwords, next: event.target.value });
              }}
              aria-describedby="new-password-strength"
              required
            />
            <PasswordStrength id="new-password-strength" value={passwords.next} />
          </label>
          <label>Confirmer le nouveau mot de passe
            <input
              type="password"
              autoComplete="new-password"
              value={passwords.confirm}
              onChange={(event) => setPasswords({ ...passwords, confirm: event.target.value })}
              required
            />
          </label>
          <button type="submit" className="primary" disabled={changePassword.isPending}>
            <span
              className={'material-symbols-outlined' + (changePassword.isPending ? ' spin' : '')}
              aria-hidden="true"
            >
              {changePassword.isPending ? 'progress_activity' : 'key'}
            </span>
            <span>{changePassword.isPending ? 'Changement…' : 'Changer le mot de passe'}</span>
          </button>
        </form>
        {passwordDone ? (
          <div className="notice" role="status">
            Mot de passe modifié. Votre session reste ouverte ; le nouveau mot de passe
            servira à votre prochaine connexion.
          </div>
        ) : null}
      </div>
    </>
  );
}
