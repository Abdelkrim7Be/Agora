import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useApi } from '../../api/useApi';
import { useStatus } from '../../contexts/StatusContext';
import { SetupProgress } from '../../components/domain/SetupProgress';
import {
  useInstanceSetupQuery,
  useStartSetup,
  useRetrySetup,
  useRetrySetupStep,
  useGmailStatusQuery,
} from '../../api/queries';

export default function SetupPage() {
  const { instanceId, currentInstance, hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { api } = useApi();
  const navigate = useNavigate();
  const canManage = hasRole('owner');
  const autoStartRef = useRef('');
  const [connecting, setConnecting] = useState(false);

  const query = useInstanceSetupQuery();
  const startSetup = useStartSetup();
  const retrySetup = useRetrySetup();
  const retryStep = useRetrySetupStep();
  const gmailQuery = useGmailStatusQuery();

  const setup = query.data;
  const gmailStatus = gmailQuery.data || {};
  const gmailConnected = gmailStatus.connection_status === 'connected';

  useEffect(() => {
    if (query.data) setStatus('Configuration chargée.', 'ok');
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Configuration indisponible : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (setup?.status !== 'ready' || !instanceId) return;
    setStatus('Configuration terminée. Les données de la boîte sont prêtes.', 'ok');
    navigate('/instance/' + instanceId, { replace: true });
  }, [setup?.status, instanceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleStart = async () => {
    try {
      await startSetup.mutateAsync();
      setStatus('Configuration démarrée.', 'ok');
    } catch (error) {
      setStatus(`Impossible de démarrer la configuration : ${error.message}`, 'error');
    }
  };

  const handleRetry = async () => {
    try {
      await retrySetup.mutateAsync();
      setStatus('Nouvelle tentative en cours.', 'ok');
    } catch (error) {
      setStatus(`Impossible de relancer la configuration : ${error.message}`, 'error');
    }
  };

  const handleRetryStep = async (stepKey) => {
    try {
      await retryStep.mutateAsync(stepKey);
      setStatus('Étape relancée.', 'ok');
    } catch (error) {
      setStatus(`Impossible de relancer l’étape : ${error.message}`, 'error');
    }
  };

  useEffect(() => {
    if (!canManage || !gmailConnected || setup?.status !== 'not_started') return;
    const key = `${instanceId}:start`;
    if (autoStartRef.current === key || startSetup.isPending) return;
    autoStartRef.current = key;
    startSetup.mutate(undefined, {
      onSuccess: () => setStatus('Configuration démarrée.', 'ok'),
      onError: (error) => setStatus(`Impossible de démarrer la configuration : ${error.message}`, 'error'),
    });
  }, [canManage, gmailConnected, setup?.status, instanceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleConnect = async () => {
    if (!instanceId || !canManage) return;
    const oauthPopup = window.open('', 'agora-gmail-connect', 'popup=yes,width=520,height=720');
    if (!oauthPopup) {
      setStatus('Popup bloquée. Autorisez les popups pour ce site puis cliquez de nouveau sur « Connecter Gmail ».', 'error');
      return;
    }
    oauthPopup.document.write('<!doctype html><title>Connexion Gmail</title><body style="font-family:system-ui,sans-serif;padding:24px;background:#0b1326;color:#dae2fd">Ouverture du consentement Google...</body>');
    setConnecting(true);
    setStatus(`Ouverture du consentement Google pour ${currentInstance?.display_name || instanceId}...`, 'ok');
    try {
      const mailbox = currentInstance?.mailbox_identity ? `?mailbox_identity=${encodeURIComponent(currentInstance.mailbox_identity)}` : '';
      const result = await api(`/api/agent/agent-instances/${encodeURIComponent(instanceId)}/connect/gmail/start${mailbox}`);
      oauthPopup.location.href = result.authorization_url;
      oauthPopup.focus();
    } catch (error) {
      oauthPopup.close();
      setConnecting(false);
      setStatus(`Impossible de démarrer la connexion Gmail : ${error.message}`, 'error');
    }
  };

  return (
    <div className="setup-gate">
      <PageHeading view="setup" />
      <Card className="setup-card">
        <div className="card-header">
          <div>
            <h2>Configuration en cours</h2>
            <div className="meta"><span>{instanceId}</span></div>
          </div>
          <div className="toolbar">
            {setup?.status === 'not_started' && canManage ? (
              <button className="primary" type="button" onClick={handleStart}>Démarrer la configuration</button>
            ) : null}
            {setup?.status === 'failed' && canManage ? (
              <button className="primary" type="button" onClick={handleRetry}>Relancer la configuration</button>
            ) : null}
          </div>
        </div>

        {query.error ? (
          <p className="empty-cell">{`Configuration indisponible : ${query.error.message}`}</p>
        ) : gmailQuery.isLoading ? (
          <p className="empty-cell">Vérification de la connexion Gmail...</p>
        ) : !gmailConnected ? (
          <div className="setup-connect-panel">
            <strong>Connecter Gmail pour démarrer cette instance.</strong>
            <p className="muted">La boîte doit être connectée avant de charger l’inbox, importer les contacts, créer les catégories et préparer les brouillons.</p>
            <button className="primary" type="button" disabled={!canManage || connecting} onClick={handleConnect}>
              <span className="material-symbols-outlined" aria-hidden="true">add_link</span>
              <span>{connecting ? 'Ouverture Google...' : 'Connecter Gmail'}</span>
            </button>
            {!canManage ? <p className="muted">Un propriétaire de l’instance doit connecter Gmail.</p> : null}
          </div>
        ) : !setup || setup.status === 'not_started' ? (
          <p className="empty-cell">
            Configuration prête à démarrer.
            {!canManage ? ' Un propriétaire doit la démarrer.' : ' Démarrage automatique en cours...'}
          </p>
        ) : (
          <>
            {setup.status === 'failed' && setup.error ? (
              <p className="setup-banner error">{setup.error}</p>
            ) : null}
            <SetupProgress setup={setup} onRetryStep={handleRetryStep} canManage={canManage} />
          </>
        )}
      </Card>
    </div>
  );
}
