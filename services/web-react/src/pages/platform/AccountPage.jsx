import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { useAuth } from '../../contexts/AuthContext';
import { useInstance, isInstanceActive } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { currentUsername } from '../../utils/jwt';

const ROLE_SUMMARY = {
  admin: 'Peut gérer les utilisateurs et consulter les vues opérationnelles de la plateforme.',
  owner: 'Peut configurer les instances, workflows, permissions, approbations et contrôles de coûts.',
  approver: 'Peut relire, prendre en charge, approuver, rejeter et répondre aux validations assignées.',
  viewer: 'Peut consulter les tableaux de bord, la boîte, les exécutions et la configuration sans les modifier.',
};

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
  const fileInputRef = useRef(null);

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
  const roleSummary = ROLE_SUMMARY[currentInstanceRole] || 'Les permissions découlent du JWT global et des délégations d’instance.';

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

  const handleSave = () => {
    updateProfile({ displayName: displayName.trim(), title: title.trim() });
    setStatus('Profil enregistré.', 'ok');
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
        <div className="notice account-role-note">
          <strong>{currentInstanceRole}</strong>: {roleSummary}
        </div>
      </div>
    </>
  );
}
