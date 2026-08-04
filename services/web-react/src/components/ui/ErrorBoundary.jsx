import { Component } from 'react';

/**
 * A render-time crash on any page used to blank the whole application: the
 * root unmounted and the client was left on a white screen with no way back.
 */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    // Keep the detail in the console for support; never put a stack on screen.
    console.error('Interface error', error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="crash-screen">
        <div className="crash-card">
          <span className="material-symbols-outlined" aria-hidden="true">error</span>
          <h1>Cette page n’a pas pu s’afficher</h1>
          <p>
            Rien n’a été modifié. Rechargez la page pour continuer ; si l’écran revient,
            signalez-le à votre administrateur.
          </p>
          <button type="button" className="primary" onClick={() => window.location.reload()}>
            Recharger la page
          </button>
        </div>
      </main>
    );
  }
}
