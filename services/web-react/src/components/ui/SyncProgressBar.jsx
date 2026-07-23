function barWidth(mode) {
  if (mode === 'syncing') return '62%';
  if (mode === 'ok' || mode === 'error') return '100%';
  return '0%';
}

export function SyncProgressBar({ label, mode, message, compact }) {
  return (
    <div className={`sync-progress${compact ? ' compact' : ''} ${mode || 'idle'}`.trim()} aria-live="polite">
      <span className="sync-spinner" aria-hidden="true"></span>
      <div>
        <strong>{label}</strong>
        <span>{message}</span>
        <div className="sync-bar" aria-hidden="true"><span style={{ width: barWidth(mode) }}></span></div>
      </div>
    </div>
  );
}
