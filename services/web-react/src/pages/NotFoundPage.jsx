import { Link } from 'react-router-dom';
import AgoraLogo from '../components/brand/AgoraLogo';

export default function NotFoundPage() {
  return (
    <main className="crash-screen">
      <div className="crash-card">
        <AgoraLogo />
        <h1>Cette page n’existe pas</h1>
        <p>
          Le lien est peut-être incomplet ou périmé. Revenez au tableau de bord pour
          retrouver vos espaces de travail.
        </p>
        <Link className="link-button" to="/">Retour au tableau de bord</Link>
      </div>
    </main>
  );
}
