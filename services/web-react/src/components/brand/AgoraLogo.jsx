export default function AgoraLogo({ compact = false, className = '' }) {
  return (
    <div className={`agora-logo ${compact ? 'compact' : ''} ${className}`.trim()}>
      <span className="agora-logo-mark" aria-hidden="true">A</span>
      {!compact ? (
        <span className="agora-logo-copy">
          <strong>Agora</strong>
          <small>Agents métiers supervisés</small>
        </span>
      ) : null}
    </div>
  );
}
