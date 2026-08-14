import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useBusy } from '../../contexts/BusyContext';
import { useDialog } from '../../contexts/DialogContext';
import { useApi } from '../../api/useApi';
import { formatDateTimeFr, statusLabelFr, friendlySyncError } from '../../utils/format';
import {
  useGmailStatusQuery,
  useGmailSyncNow,
  useGmailPause,
  useGmailResume,
  useGmailDisconnect,
  useMailboxConnectionTest,
  useRuntimeSettingsQuery,
  useSaveRuntimeSettings
} from '../../api/queries';

function connectionStatusClass(status) {
  if (status === 'connected') return 'ok';
  if (status === 'expired') return 'warn';
  if (status === 'error') return 'error';
  return '';
}

function hasCurrentSyncFailure(status = {}) {
  if (!status.last_error) return false;
  const successAt = status.last_success_at ? new Date(status.last_success_at).getTime() : 0;
  const failureAt = status.last_failure_at ? new Date(status.last_failure_at).getTime() : 0;
  return !successAt || !failureAt || failureAt >= successAt;
}

function barWidth(mode) {
  if (mode === 'syncing') return '62%';
  if (mode === 'ok' || mode === 'error') return '100%';
  return '0%';
}

export default function GmailPage() {
  const { instanceId, currentInstance, hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { runBusy } = useBusy();
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
  const testConnection = useMailboxConnectionTest();
  const runtimeSettings = useRuntimeSettingsQuery();
  const saveRuntimeSettings = useSaveRuntimeSettings();

  const status = query.data || {};
  // The agent owns the provider setting — it is the component holding the token.
  // Anything unset reads as Gmail, which is what every pre-Outlook instance is.
  const provider = status.provider === 'outlook' ? 'outlook' : 'gmail';
  const providerLabel = provider === 'outlook' ? 'Outlook' : 'Gmail';
  const connected = status.connection_status === 'connected';
  const wasConnected = status.connection_status === 'disconnected' || status.connection_status === 'error';

  useEffect(() => {
    if (!query.data) return;
    const lastSuccess = status.last_success_at ? `Dernière synchronisation réussie ${new Date(status.last_success_at).toLocaleTimeString('fr-FR')}.` : 'Aucune synchronisation terminée pour l’instant.';
    const lastFailure = status.last_error ? `Dernière synchronisation échouée : ${friendlySyncError(status.last_error)}` : lastSuccess;
    const currentFailure = hasCurrentSyncFailure(status);
    setVisual({
      mode: status.connection_status === 'error' || currentFailure ? 'error' : 'idle',
      message: status.connection_status === 'connected' && !currentFailure ? lastSuccess : lastFailure,
    });
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (runtimeSettings.data) setRuntimeForm(runtimeSettings.data);
  }, [runtimeSettings.data]);

  const RUNTIME_FIELD_BOUNDS = {
    sync_limit: { min: 1, max: 500 },
    setup_recent_limit: { min: 1, max: 500 },
    setup_backlog_limit: { min: 1, max: 500 },
    setup_sent_sample: { min: 1, max: 500 },
    // Fed whole into a single style-learning prompt (not one call per sample like the
    // others above) — a small local model's context window caps this well below 500.
    style_sent_sample: { min: 1, max: 50 },
  };

  const updateRuntimeField = (field, value) => {
    const parsed = Number.parseInt(value, 10);
    const bounds = RUNTIME_FIELD_BOUNDS[field];
    const clamped = Number.isNaN(parsed) ? '' : Math.min(bounds.max, Math.max(bounds.min, parsed));
    setRuntimeForm((current) => ({
      ...(current || runtimeSettings.data || {}),
      [field]: clamped,
    }));
  };

  const handleSaveRuntimeSettings = async () => {
    if (!runtimeForm) return;
    try {
      await runBusy('Enregistrement des réglages', () => saveRuntimeSettings.mutateAsync(runtimeForm));
      setStatus('Paramètres d’analyse enregistrés.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer les paramètres d’analyse : ${error.message}`, 'error');
    }
  };

  // Open provider OAuth in a popup and keep the workspace visible. Do not use
  // noopener here: Chromium returns null for the popup handle, which can make
  // popup-blocker detection look like a failure even after the window opened.
  const handleConnect = async () => {
    if (!instanceId) return;
    const oauthPopup = window.open('', 'agora-mailbox-connect', 'popup=yes,width=520,height=720');
    if (!oauthPopup) {
      setStatus(`Popup bloquée. Autorisez les popups pour ce site puis cliquez de nouveau sur « Connecter ${providerLabel} ».`, 'error');
      return;
    }
    oauthPopup.document.write(`<!doctype html><title>Connexion ${providerLabel}</title><body style="font-family:system-ui,sans-serif;padding:24px;background:#0b1326;color:#dae2fd">Ouverture du consentement ${providerLabel}...</body>`);
    setConnecting(true);
    setVisual({ mode: 'syncing', message: `Ouverture du consentement ${providerLabel} pour ${currentInstance?.display_name || instanceId}...` });
    setStatus(`Ouverture du consentement ${providerLabel} pour ${currentInstance?.display_name || instanceId}...`, 'ok');
    try {
      const mailbox = currentInstance?.mailbox_identity ? `?mailbox_identity=${encodeURIComponent(currentInstance.mailbox_identity)}` : '';
      const result = await api(`/api/agent/agent-instances/${encodeURIComponent(instanceId)}/connect/${provider}/start${mailbox}`);
      oauthPopup.location.href = result.authorization_url;
      oauthPopup.focus();
    } catch (error) {
      oauthPopup.close();
      setConnecting(false);
      setVisual({ mode: 'error', message: `Impossible de démarrer la connexion ${providerLabel} : ${error.message}` });
      setStatus(`Impossible de démarrer la connexion ${providerLabel} : ${error.message}`, 'error');
    }
  };

  const handleTestConnection = async () => {
    setVisual({ mode: 'syncing', message: `Test de la connexion ${providerLabel} en cours...` });
    setStatus(`Test de la connexion ${providerLabel}...`, 'ok');
    try {
      const result = await runBusy('Test de la connexion à la boîte', () => testConnection.mutateAsync());
      if (result.ok) {
        const mailbox = result.mailbox ? ` (${result.mailbox})` : '';
        setStatus(`Connexion ${providerLabel} opérationnelle${mailbox}.`, 'ok');
        setVisual({ mode: 'ok', message: `La boîte répond${mailbox}.` });
      } else {
        setStatus(`La boîte ne répond pas : ${result.error}`, 'error');
        setVisual({ mode: 'error', message: `La boîte ne répond pas : ${result.error}` });
      }
    } catch (error) {
      setStatus(`Impossible de tester la connexion : ${error.message}`, 'error');
      setVisual({ mode: 'error', message: `Impossible de tester la connexion : ${error.message}` });
    }
  };

  const handleSyncNow = async () => {
    setVisual({ mode: 'syncing', message: 'Synchronisation Gmail en cours. Lecture des messages non lus et préparation des validations...' });
    setStatus('Synchronisation de la boîte Gmail...', 'ok');
    try {
      await runBusy('Synchronisation de la boîte', () => syncNow.mutateAsync());
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
      title: `Déconnecter ${providerLabel}`,
      message: 'Supprime le jeton OAuth stocké pour cette instance. L’agent cessera de synchroniser jusqu’à la reconnexion.',
      confirmLabel: 'Déconnecter',
      confirmIcon: 'link_off',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await runBusy('Déconnexion de la boîte', () => disconnect.mutateAsync(provider));
      setStatus(`${providerLabel} déconnecté. Jeton OAuth supprimé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de déconnecter ${providerLabel} : ${error.message}`, 'error');
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
          <button className="primary" type="button" disabled={connected || connecting} title={connected ? `${providerLabel} est déjà connecté pour cette boîte` : `Connecter ${providerLabel}`} onClick={handleConnect}>
            <span className="material-symbols-outlined" aria-hidden="true">add_link</span>
            <span>{connected ? `${providerLabel} connecté` : (wasConnected ? `Reconnecter ${providerLabel}` : `Connecter ${providerLabel}`)}</span>
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
          <div><span>Fournisseur</span><strong>{providerLabel}</strong></div>
          <div><span>Connexion</span><strong className={connectionStatusClass(status.connection_status)}>{status.connection_status ? statusLabelFr(status.connection_status) : '—'}</strong></div>
          <div><span>Mode de synchro</span><strong>{status.sync_mode || '—'}</strong></div>
          <div><span>Dernier succès</span><strong>{formatDateTimeFr(status.last_success_at)}</strong></div>
          <div><span>Dernier échec</span><strong>{formatDateTimeFr(status.last_failure_at)}</strong></div>
          <div><span>Expiration du watch</span><strong>{formatDateTimeFr(status.watch_expires_at)}</strong></div>
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
          <button type="button" disabled={testConnection.isPending} onClick={handleTestConnection}>
            <span className="material-symbols-outlined" aria-hidden="true">network_check</span>
            <span>{testConnection.isPending ? 'Test en cours…' : 'Tester la connexion'}</span>
          </button>
          <button className="danger" type="button" onClick={handleDisconnect}>
            <span className="material-symbols-outlined" aria-hidden="true">link_off</span><span>Déconnecter {providerLabel}</span>
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
                <input type="number" min="1" max="500" value={runtimeForm.sync_limit ?? ''} onChange={(event) => updateRuntimeField('sync_limit', event.target.value)} />
              </label>
              <label>
                <span>E-mails récents au démarrage</span>
                <input type="number" min="1" max="500" value={runtimeForm.setup_recent_limit ?? ''} onChange={(event) => updateRuntimeField('setup_recent_limit', event.target.value)} />
              </label>
              <label>
                <span>Non lus traités au démarrage</span>
                <input type="number" min="1" max="500" value={runtimeForm.setup_backlog_limit ?? ''} onChange={(event) => updateRuntimeField('setup_backlog_limit', event.target.value)} />
              </label>
              <label>
                <span>Envoyés lus au démarrage</span>
                <input type="number" min="1" max="500" value={runtimeForm.setup_sent_sample ?? ''} onChange={(event) => updateRuntimeField('setup_sent_sample', event.target.value)} />
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
