/**
 * One figure in a stat row.
 *
 * `size="sm"` is for values that are not headline figures — a timestamp, a
 * sentence. At the display size those filled their card and out-shouted the
 * numbers beside them, which inverted the reading order of the whole row.
 */
export function MetricTile({ label, value, hint, size = 'lg' }) {
  return (
    <div className={'metric' + (size === 'sm' ? ' metric-sm' : '')} title={hint || undefined}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small className="metric-hint">{hint}</small> : null}
    </div>
  );
}
