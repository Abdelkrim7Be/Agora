export function MetricTile({ label, value, hint }) {
  return (
    <div className="metric" title={hint || undefined}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small className="metric-hint">{hint}</small> : null}
    </div>
  );
}
