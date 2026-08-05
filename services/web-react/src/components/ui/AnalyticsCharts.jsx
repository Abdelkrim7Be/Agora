import { workflowLabelFr } from '../../utils/format';

/** Ticks a person reads without decoding: 0, half, top — rounded to whole mails. */
function axisTicks(maxValue) {
  if (maxValue <= 1) return [0, 1];
  const mid = Math.round(maxValue / 2);
  return mid === 0 || mid === maxValue ? [0, maxValue] : [0, mid, maxValue];
}

export function VerticalBarChart({ points, emptyLabel }) {
  const rows = (points || []).filter((point) => Number(point.count || 0) >= 0);
  const maxValue = Math.max(0, ...rows.map((point) => Number(point.count || 0)));
  if (!rows.length || maxValue === 0) return <div className="empty">{emptyLabel}</div>;

  const width = 560;
  const height = 220;
  const left = 34;
  const right = 16;
  const top = 18;
  const bottom = 44;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const gap = Math.max(4, Math.floor(chartWidth / Math.max(rows.length, 1) / 5));
  const barWidth = Math.max(10, Math.floor((chartWidth - gap * (rows.length - 1)) / rows.length));
  const labelEvery = rows.length > 10 ? 2 : 1;
  const ticks = axisTicks(maxValue);
  const lastIndex = rows.length - 1;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Volume traité sur la période">
      {/* A scale, so a lone tall bar means something instead of just being tall. */}
      {ticks.map((tick) => {
        const y = top + chartHeight - (maxValue ? (tick / maxValue) * chartHeight : 0);
        return (
          <g key={tick}>
            <line
              x1={left}
              y1={y}
              x2={width - right}
              y2={y}
              stroke="var(--outline)"
              strokeWidth="1"
              strokeDasharray={tick === 0 ? undefined : '3 4'}
              opacity={tick === 0 ? 1 : 0.6}
            />
            <text x={left - 8} y={y + 4} textAnchor="end" fontSize="10" fill="var(--muted)">{tick}</text>
          </g>
        );
      })}

      {rows.map((point, index) => {
        const value = Number(point.count || 0);
        const x = left + index * (barWidth + gap);
        const barHeight = maxValue ? Math.round((value / maxValue) * chartHeight) : 0;
        const y = top + chartHeight - barHeight;
        const isLatest = index === lastIndex;
        return (
          <g key={point.label ?? index}>
            {/* An empty day is empty — the old 2px stub with a "0" over it read as
                a tiny bar and made six quiet days look like six data points. */}
            {value > 0 ? (
              <>
                <rect
                  x={x}
                  y={y}
                  width={barWidth}
                  height={Math.max(barHeight, 3)}
                  rx="5"
                  fill={isLatest ? 'var(--primary)' : 'color-mix(in srgb, var(--primary) 62%, transparent)'}
                />
                <text x={x + barWidth / 2} y={Math.max(y - 6, 12)} textAnchor="middle" fontSize="11" fill="var(--text)">
                  {value}
                </text>
              </>
            ) : null}
            {index % labelEvery === 0 && (
              <text
                x={x + barWidth / 2}
                y={height - 16}
                textAnchor="middle"
                fontSize="11"
                fill={isLatest ? 'var(--text)' : 'var(--muted)'}
              >
                {point.label || ''}
              </text>
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
  const rowHeight = 32;
  const height = items.length * rowHeight + 16;
  const left = 176;
  const right = 44;
  const barWidth = width - left - right;
  const total = items.reduce((sum, row) => sum + Number(row.count || 0), 0);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Cas métier les plus actifs sur la période">
      {items.map((row, index) => {
        const value = Number(row.count || 0);
        const y = 8 + index * rowHeight;
        const widthPx = Math.max(6, Math.round((value / maxValue) * barWidth));
        const share = total ? Math.round((value / total) * 100) : 0;
        const label = workflowLabelFr(row);
        return (
          <g key={row.category ?? row.display_name ?? index}>
            <title>{`${label} — ${value} e-mail(s), ${share} % de la période`}</title>
            <text x="8" y={y + 15} fontSize="12" fill="var(--text)">
              {label.length > 26 ? `${label.slice(0, 25)}…` : label}
            </text>
            <rect x={left} y={y + 2} width={barWidth} height="14" rx="7" fill="var(--surface-high)" />
            <rect x={left} y={y + 2} width={widthPx} height="14" rx="7" fill="var(--primary)" />
            <text x={width - 8} y={y + 13} textAnchor="end" fontSize="12" fill="var(--text)">{value}</text>
            <text x="8" y={y + 27} fontSize="10" fill="var(--muted)">{share} %</text>
          </g>
        );
      })}
    </svg>
  );
}
