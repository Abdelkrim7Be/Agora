import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { useAuth } from '../../contexts/AuthContext';
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
  useAgentInstancesQuery,
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
  const { gatewayBase } = useAuth();
  const { instanceId, currentInstance, hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const { api } = useApi();
  const canManage = hasRole('owner');

  const [visual, setVisual] = useState({ mode: 'idle', message: 'Synchronisation Gmail en veille.' });
  const [connecting, setConnecting] = useState(false);
  const popupRef = useRef(null);
  const popupTimerRef = useRef(null);

  const query = useGmailStatusQuery();
  const instancesQuery = useAgentInstancesQuery();
  const syncNow = useGmailSyncNow();
  const pause = useGmailPause();
  const resume = useGmailResume();
  const disconnect = useGmailDisconnect();

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

  // Gmail OAuth popup postMessage handshake.
  useEffect(() => {
    const handler = (event) => {
      const allowedOrigins = new Set([window.location.origin]);
      if (gatewayBase) {
        try { allowedOrigins.add(new URL(gatewayBase).origin); } catch (_error) { /* ignore invalid local gateway URL */ }
      }
      ['8080', '8090'].forEach((port) => allowedOrigins.add(`${window.location.protocol}//${window.location.hostname}:${port}`));
      if (!allowedOrigins.has(event.origin)) return;
      const data = event.data || {};
      if (data.type !== 'agora:gmail-oauth') return;
      if (popupTimerRef.current) { window.clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
      setConnecting(false);
      if (data.status === 'connected') {
        setStatus('Gmail connecté. Actualisation du statut de la boîte...', 'ok');
        setVisual({ mode: 'ok', message: 'Gmail connecté. La synchronisation peut démarrer.' });
        query.refetch();
        instancesQuery.refetch();
      } else {
        setStatus(`Connexion Gmail échouée : ${data.message || 'Erreur inconnue'}`, 'error');
        setVisual({ mode: 'error', message: data.message || 'Connexion Gmail échouée.' });
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }); // intentionally no deps — always reads latest gatewayBase/instanceId via closure

  const handleConnect = async () => {
    if (!instanceId) return;
    const popup = window.open('', 'agora-gmail-oauth', 'popup,width=720,height=760');
    if (!popup) {
      setStatus('Popup bloquée. Autorisez les popups pour ce site puis cliquez de nouveau sur « Connecter Gmail ».', 'error');
      return;
    }
    popup.document.write('<!doctype html><title>Ouverture de Gmail</title><body style="font-family:system-ui,sans-serif;padding:24px;background:#0b1326;color:#dae2fd">Ouverture du consentement Google...</body>');
    popup.focus();
    popupRef.current = popup;
    setConnecting(true);
    setVisual({ mode: 'syncing', message: `Ouverture du consentement Google pour ${currentInstance?.display_name || instanceId}...` });
    setStatus(`Ouverture du consentement Google pour ${currentInstance?.display_name || instanceId}...`, 'ok');
    try {
      const mailbox = currentInstance?.mailbox_identity ? `?mailbox_identity=${encodeURIComponent(currentInstance.mailbox_identity)}` : '';
      const result = await api(`/api/agent/agent-instances/${encodeURIComponent(instanceId)}/connect/gmail/start${mailbox}`);
      popup.location.href = result.authorization_url;
      setVisual({ mode: 'syncing', message: 'En attente de l’autorisation Google...' });
      if (popupTimerRef.current) window.clearInterval(popupTimerRef.current);
      popupTimerRef.current = window.setInterval(() => {
        if (popup.closed) {
          window.clearInterval(popupTimerRef.current);
          popupTimerRef.current = null;
          setConnecting(false);
          setVisual({ mode: 'idle', message: 'Fenêtre d’autorisation Gmail fermée. Actualisation du statut...' });
          query.refetch();
          instancesQuery.refetch();
        }
      }, 800);
    } catch (error) {
      popup.close();
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
        <div className="notice" style={{ marginTop: '1rem' }}>
          <strong>Données &amp; confidentialité :</strong> le contenu des e-mails est envoyé au fournisseur LLM configuré pour le triage, la rédaction ou l'apprentissage du style. Déconnecter Gmail supprime le jeton OAuth stocké pour cette instance. Le style et la mémoire s'effacent séparément.
        </div>
      </div>
    </>
  );
}
