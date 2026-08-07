import { useEffect } from 'react';
import { useBusy } from '../../contexts/BusyContext';

function barWidth(mode) {
  if (mode === 'syncing') return '62%';
  if (mode === 'ok' || mode === 'error') return '100%';
  return '0%';
}

export function SyncProgressBar({ label, mode, message, compact }) {
  const busy = useBusy();
  const running = mode === 'syncing';

  // While this bar is reporting on the work, the global overlay stands down —
  // it was covering the bar the person was watching, on exactly the actions
  // that take long enough to be worth watching.
  useEffect(() => {
    if (!running || !busy?.holdOverlay) return undefined;
    return busy.holdOverlay();
  }, [running, busy]);

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
