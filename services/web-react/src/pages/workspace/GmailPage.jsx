import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useApi } from '../../api/useApi';
import { statusLabelFr, friendlySyncError } from '../../utils/format';
import {
  useGmailStatusQuery,
  useGmailSyncNow,
  useGmailPause,
  useGmailResume,
  useGmailDisconnect,
  useRuntimeSettingsQuery,
  useSaveRuntimeSettings
} from '../../api/queries';

function connectionStatusClass(status) {
  if (status === 'connected') return 'ok';
  if (status === 'expired') return 'warn';
  if (status === 'error') return 'error';
  return '';
}

function barWidth(mode) {
  if (mode === 'syncing') return '62%';
  if (mode === 'ok' || mode === 'error') return '100%';
  return '0%';
}

export default function GmailPage() {
  const { instanceId, currentInstance, hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const { api } = useApi();
  const canManage = hasRole('owner');

  const [visual, setVisual] = useState({ mode: 'idle', message: 'Synchronisation Gmail en veille.' });
  const [connecting, setConnecting] = useState(false);
  const [runtimeForm, setRuntimeForm] = useState(null);

  const query = useGmailStatusQuery();
  const syncNow = useGmailSyncNow();
  const pause = useGmailPause();
  const resume = useGmailResume();
  const disconnect = useGmailDisconnect();
  const runtimeSettings = useRuntimeSettingsQuery();
  const saveRuntimeSettings = useSaveRuntimeSettings();

  const status = query.data || {};
  const connected = status.connection_status === 'connected';
  const wasConnected = status.connection_status === 'disconnected' || status.connection_status === 'error';

  useEffect(() => {
    if (!query.data) return;
    const lastSuccess = status.last_success_at ? `Dernière synchronisation réussie ${new Date(status.last_success_at).toLocaleTimeString()}.` : 'Aucune synchronisation terminée pour l’instant.';
    const lastFailure = status.last_error ? `Dernière synchronisation échouée : ${friendlySyncError(status.last_error)}` : lastSuccess;
    setVisual({
      mode: status.connection_status === 'error' || status.last_error ? 'error' : 'idle',
      message: status.connection_status === 'connected' ? lastSuccess : lastFailure,
    });
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (runtimeSettings.data) setRuntimeForm(runtimeSettings.data);
  }, [runtimeSettings.data]);

  const updateRuntimeField = (field, value) => {
    const parsed = Number.parseInt(value, 10);
    setRuntimeForm((current) => ({
      ...(current || runtimeSettings.data || {}),
      [field]: Number.isNaN(parsed) ? '' : parsed,
    }));
  };

  const handleSaveRuntimeSettings = async () => {
    if (!runtimeForm) return;
    try {
      await saveRuntimeSettings.mutateAsync(runtimeForm);
      setStatus('Paramètres d’analyse enregistrés.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer les paramètres d’analyse : ${error.message}`, 'error');
    }
  };

  // Open Google OAuth in a popup and keep the workspace visible. Do not use
  // noopener here: Chromium returns null for the popup handle, which can make
  // popup-blocker detection look like a failure even after the window opened.
  const handleConnect = async () => {
    if (!instanceId) return;
    const oauthPopup = window.open('', 'agora-gmail-connect', 'popup=yes,width=520,height=720');
    if (!oauthPopup) {
      setStatus('Popup bloquée. Autorisez les popups pour ce site puis cliquez de nouveau sur « Connecter Gmail ».', 'error');
      return;
    }
    oauthPopup.document.write('<!doctype html><title>Connexion Gmail</title><body style="font-family:system-ui,sans-serif;padding:24px;background:#0b1326;color:#dae2fd">Ouverture du consentement Google...</body>');
    setConnecting(true);
    setVisual({ mode: 'syncing', message: `Ouverture du consentement Google pour ${currentInstance?.display_name || instanceId}...` });
    setStatus(`Ouverture du consentement Google pour ${currentInstance?.display_name || instanceId}...`, 'ok');
    try {
      const mailbox = currentInstance?.mailbox_identity ? `?mailbox_identity=${encodeURIComponent(currentInstance.mailbox_identity)}` : '';
      const result = await api(`/api/agent/agent-instances/${encodeURIComponent(instanceId)}/connect/gmail/start${mailbox}`);
      oauthPopup.location.href = result.authorization_url;
      oauthPopup.focus();
    } catch (error) {
      oauthPopup.close();
      setConnecting(false);
      setVisual({ mode: 'error', message: `Impossible de démarrer la connexion Gmail : ${error.message}` });
      setStatus(`Impossible de démarrer la connexion Gmail : ${error.message}`, 'error');
    }
  };

  const handleSyncNow = async () => {
    setVisual({ mode: 'syncing', message: 'Synchronisation Gmail en cours. Lecture des messages non lus et préparation des validations...' });
    setStatus('Synchronisation de la boîte Gmail...', 'ok');
    try {
      await syncNow.mutateAsync();
      setStatus('Synchronisation Gmail terminée.', 'ok');
      setVisual({ mode: 'ok', message: 'Synchronisation Gmail terminée. Les brouillons et les messages sont à jour.' });
    } catch (error) {
      setStatus(`Échec de synchronisation : ${friendlySyncError(error.message)}`, 'error');
      setVisual({ mode: 'error', message: `Échec de synchronisation : ${friendlySyncError(error.message)}` });
    }
  };

  const handlePause = async () => {
    try {
      await pause.mutateAsync();
      setStatus('Synchronisation Gmail en pause.', 'ok');
    } catch (error) {
      setStatus(`Impossible de mettre la synchronisation en pause : ${error.message}`, 'error');
    }
  };

  const handleResume = async () => {
    try {
      await resume.mutateAsync();
      setStatus('Synchronisation Gmail reprise.', 'ok');
    } catch (error) {
      setStatus(`Impossible de reprendre la synchronisation : ${error.message}`, 'error');
    }
  };

  const handleDisconnect = async () => {
    const confirmed = await confirmDialog({
      title: 'Déconnecter Gmail',
      message: 'Supprime le jeton OAuth stocké pour cette instance. L’agent cessera de synchroniser jusqu’à la reconnexion.',
      confirmLabel: 'Déconnecter',
      confirmIcon: 'link_off',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await disconnect.mutateAsync();
      setStatus('Gmail déconnecté. Jeton OAuth supprimé.', 'ok');
    } catch (error) {
      setStatus(`Impossible de déconnecter Gmail : ${error.message}`, 'error');
    }
  };

  const paused = Boolean(status.paused);

  return (
    <>
      <PageHeading view="gmail" />
      <div className="card">
        <div className="card-header">
          <div>
            <h2>Connexion et synchronisation Gmail</h2>
            <div className="meta"><span>{currentInstance?.display_name || instanceId}</span></div>
          </div>
          <button className="primary" type="button" disabled={connected || connecting} title={connected ? 'Gmail est déjà connecté pour cette boîte' : 'Connecter Gmail'} onClick={handleConnect}>
            <span className="material-symbols-outlined" aria-hidden="true">add_link</span>
            <span>{connected ? 'Gmail connecté' : (wasConnected ? 'Reconnecter Gmail' : 'Connecter Gmail')}</span>
          </button>
        </div>
        <div className="notice">
          Connecte la boîte sélectionnée à Gmail pour cette instance d’agent. L’apprentissage du style reste optionnel et les contrôles de déconnexion ou d’effacement restent séparés.
        </div>
        <div className={`sync-progress ${visual.mode}`.trim()} aria-live="polite">
          <span className="sync-spinner" aria-hidden="true"></span>
          <div>
            <strong>Synchronisation Gmail</strong>
            <span>{visual.message}</span>
            <div className="sync-bar" aria-hidden="true"><span style={{ width: barWidth(visual.mode) }}></span></div>
          </div>
        </div>
        <div className="summary-grid">
          <div><span>Connexion</span><strong className={connectionStatusClass(status.connection_status)}>{status.connection_status ? statusLabelFr(status.connection_status) : '—'}</strong></div>
          <div><span>Mode de synchro</span><strong>{status.sync_mode || '—'}</strong></div>
          <div><span>Dernier succès</span><strong>{status.last_success_at ? new Date(status.last_success_at).toLocaleString() : '—'}</strong></div>
          <div><span>Dernier échec</span><strong>{status.last_failure_at ? new Date(status.last_failure_at).toLocaleString() : '—'}</strong></div>
          <div><span>Expiration du watch</span><strong>{status.watch_expires_at ? new Date(status.watch_expires_at).toLocaleString() : '—'}</strong></div>
          <div><span>En pause</span><strong>{status.paused ? 'Oui' : 'Non'}</strong></div>
        </div>
        {status.last_error ? (
          <div className="notice error">
            <strong>Dernière erreur :</strong> {friendlySyncError(status.last_error)}
          </div>
        ) : null}
        <div className="toolbar" style={{ marginTop: '1rem' }}>
          <button type="button" onClick={handleSyncNow}>
            <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Synchroniser</span>
          </button>
          <button type="button" disabled={!canManage || !connected || paused} title={paused ? 'La synchronisation est déjà en pause' : 'Mettre la synchronisation en pause'} onClick={handlePause}>
            <span className="material-symbols-outlined" aria-hidden="true">pause</span><span>Pause</span>
          </button>
          <button type="button" disabled={!canManage || !connected || !paused} title={paused ? 'Reprendre la synchronisation' : 'La synchronisation est déjà active'} onClick={handleResume}>
            <span className="material-symbols-outlined" aria-hidden="true">play_arrow</span><span>Reprendre</span>
          </button>
          <button type="button" onClick={() => query.refetch()}>
            <span className="material-symbols-outlined" aria-hidden="true">refresh</span><span>Actualiser le statut</span>
          </button>
          <button className="danger" type="button" onClick={handleDisconnect}>
            <span className="material-symbols-outlined" aria-hidden="true">link_off</span><span>Déconnecter Gmail</span>
          </button>
        </div>
        {canManage && runtimeForm ? (
          <div className="card soft" style={{ marginTop: '1rem' }}>
            <div className="card-header">
              <div>
                <h3>Paramètres d’analyse</h3>
                <div className="meta">Ces nombres pilotent la synchronisation, le démarrage, les brouillons et l’apprentissage de style pour cette instance.</div>
              </div>
              <button className="primary" type="button" disabled={saveRuntimeSettings.isPending} onClick={handleSaveRuntimeSettings}>
                <span className="material-symbols-outlined" aria-hidden="true">save</span>
                <span>Enregistrer</span>
              </button>
            </div>
            <div className="settings-grid">
              <label>
                <span>Messages par synchronisation</span>
                <input type="number" min="1" max="100" value={runtimeForm.sync_limit ?? ''} onChange={(event) => updateRuntimeField('sync_limit', event.target.value)} />
              </label>
              <label>
                <span>E-mails récents au démarrage</span>
                <input type="number" min="1" max="200" value={runtimeForm.setup_recent_limit ?? ''} onChange={(event) => updateRuntimeField('setup_recent_limit', event.target.value)} />
              </label>
              <label>
                <span>Non lus traités au démarrage</span>
                <input type="number" min="1" max="100" value={runtimeForm.setup_backlog_limit ?? ''} onChange={(event) => updateRuntimeField('setup_backlog_limit', event.target.value)} />
              </label>
              <label>
                <span>Envoyés lus au démarrage</span>
                <input type="number" min="1" max="200" value={runtimeForm.setup_sent_sample ?? ''} onChange={(event) => updateRuntimeField('setup_sent_sample', event.target.value)} />
              </label>
              <label>
                <span>Envoyés pour apprendre le style</span>
                <input type="number" min="1" max="50" value={runtimeForm.style_sent_sample ?? ''} onChange={(event) => updateRuntimeField('style_sent_sample', event.target.value)} />
              </label>
            </div>
          </div>
        ) : null}
        <div className="notice" style={{ marginTop: '1rem' }}>
          <strong>Données &amp; confidentialité :</strong> le contenu des e-mails est envoyé au fournisseur LLM configuré pour le triage, la rédaction ou l'apprentissage du style. Déconnecter Gmail supprime le jeton OAuth stocké pour cette instance. Le style et la mémoire s'effacent séparément.
        </div>
      </div>
    </>
  );
}
