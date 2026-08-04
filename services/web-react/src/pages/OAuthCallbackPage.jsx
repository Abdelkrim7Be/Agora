import { useEffect } from 'react';

export default function OAuthCallbackPage() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get('gmail') || 'error';
    const agentInstanceId = params.get('agent_instance_id') || '';
    const message = params.get('message') || '';
    const payload = {
      type: 'agora:gmail-oauth-complete',
      status,
      agentInstanceId,
      message,
      at: Date.now(),
    };
    try {
      window.localStorage.setItem('agora:gmail-oauth-complete', JSON.stringify(payload));
    } catch (_error) {
      // Ignore storage failures; postMessage and polling still cover the main window.
    }
    try {
      window.opener?.postMessage(payload, window.location.origin);
    } catch (_error) {
      // Some browser policies can deny opener access after OAuth redirects.
    }
    window.close();
  }, []);

  return (
    <main className="oauth-complete">
      <h1>Connexion Gmail terminée</h1>
      <p>Vous pouvez fermer cette fenêtre et revenir à la configuration.</p>
    </main>
  );
}
