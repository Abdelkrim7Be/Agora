import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { SetupProgress } from '../../components/domain/SetupProgress';
import {
  useInstanceSetupQuery,
  useStartSetup,
  useRetrySetup,
  useRetrySetupStep,
  useSkipSetup,
} from '../../api/queries';

export default function SetupPage() {
  const { instanceId, hasRole } = useInstance();
  const { setStatus } = useStatus();
  const canManage = hasRole('owner');

  const query = useInstanceSetupQuery();
  const startSetup = useStartSetup();
  const retrySetup = useRetrySetup();
  const retryStep = useRetrySetupStep();
  const skipSetup = useSkipSetup();

  const setup = query.data;

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

  const handleSkip = async () => {
    try {
      await skipSetup.mutateAsync();
      setStatus('Configuration passée.', 'ok');
    } catch (error) {
      setStatus(`Impossible de passer la configuration : ${error.message}`, 'error');
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
            {canManage && setup?.status !== 'ready' ? (
              <button type="button" className="ghost" onClick={handleSkip}>Passer la configuration</button>
            ) : null}
          </div>
        </div>

        {query.error ? (
          <p className="empty-cell">{`Configuration indisponible : ${query.error.message}`}</p>
        ) : !setup || setup.status === 'not_started' ? (
          <p className="empty-cell">
            La configuration n’a pas encore démarré pour cette instance.
            {!canManage ? ' Un propriétaire doit la démarrer.' : ''}
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
