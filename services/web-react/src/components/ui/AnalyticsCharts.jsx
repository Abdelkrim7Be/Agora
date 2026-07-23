import { workflowLabelFr } from '../../utils/format';

export function VerticalBarChart({ points, emptyLabel }) {
  const rows = (points || []).filter((point) => Number(point.count || 0) >= 0);
  const maxValue = Math.max(0, ...rows.map((point) => Number(point.count || 0)));
  if (!rows.length || maxValue === 0) return <div className="empty">{emptyLabel}</div>;

  const width = 560;
  const height = 220;
  const left = 24;
  const right = 20;
  const top = 16;
  const bottom = 48;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const gap = Math.max(4, Math.floor(chartWidth / Math.max(rows.length, 1) / 5));
  const barWidth = Math.max(10, Math.floor((chartWidth - gap * (rows.length - 1)) / rows.length));
  const labelEvery = rows.length > 10 ? 2 : 1;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Volume traité sur la période">
      <line x1={left} y1={top + chartHeight} x2={width - right} y2={top + chartHeight} stroke="#cbd5e1" strokeWidth="1" />
      {rows.map((point, index) => {
        const value = Number(point.count || 0);
        const x = left + index * (barWidth + gap);
        const barHeight = maxValue ? Math.round((value / maxValue) * chartHeight) : 0;
        const y = top + chartHeight - barHeight;
        return (
          <g key={point.label ?? index}>
            <rect x={x} y={y} width={barWidth} height={Math.max(barHeight, 2)} rx="6" fill="#2563eb" />
            <text x={x + barWidth / 2} y={Math.max(y - 6, 12)} textAnchor="middle" fontSize="11" fill="#0f172a">{value}</text>
            {index % labelEvery === 0 && (
              <text x={x + barWidth / 2} y={height - 18} textAnchor="middle" fontSize="11" fill="#6b7280">{point.label || ''}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function HorizontalBarChart({ rows, emptyLabel }) {
  const items = (rows || []).slice(0, 5);
  const maxValue = Math.max(0, ...items.map((row) => Number(row.count || 0)));
  if (!items.length || maxValue === 0) return <div className="empty">{emptyLabel}</div>;

  const width = 560;
  const rowHeight = 28;
  const height = items.length * rowHeight + 20;
  const left = 170;
  const right = 50;
  const barWidth = width - left - right;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Top workflows sur la période">
      {items.map((row, index) => {
        const value = Number(row.count || 0);
        const y = 8 + index * rowHeight;
        const widthPx = Math.max(6, Math.round((value / maxValue) * barWidth));
        return (
          <g key={row.category ?? row.display_name ?? index}>
            <text x="10" y={y + 15} fontSize="12" fill="#0f172a">{workflowLabelFr(row)}</text>
            <rect x={left} y={y} width={barWidth} height="16" rx="8" fill="#e2e8f0" />
            <rect x={left} y={y} width={widthPx} height="16" rx="8" fill="#0f766e" />
            <text x={left + widthPx + 8} y={y + 13} fontSize="12" fill="#0f172a">{value}</text>
          </g>
        );
      })}
    </svg>
  );
}
