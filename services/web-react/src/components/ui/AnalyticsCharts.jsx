import { workflowLabelFr } from '../../utils/format';

/** Log-spaced ticks: a linear 0/half/top scale puts every quiet day at the
 * baseline once one day dominates the period (e.g. a bulk-send spike). Ticks
 * a decade apart keep small values readable next to a much larger one. */
function axisTicks(maxValue) {
  if (maxValue <= 1) return [0, Math.max(1, maxValue)];
  const ticks = new Set([0, maxValue]);
  let step = maxValue;
  for (let i = 0; i < 3; i += 1) {
    step = Math.round(step / 10);
    if (step < 1) break;
    ticks.add(step);
  }
  return Array.from(ticks).sort((a, b) => a - b);
}

/** Position on a log1p scale — compresses a dominant outlier so the rest of
 * the series still has usable pixel range instead of sitting on the axis. */
function logShare(value, maxValue) {
  if (!maxValue || value <= 0) return 0;
  return Math.log1p(value) / Math.log1p(maxValue);
}

/** "0 %" reads as literally nothing; a share that rounds to zero is still
 * worth showing as a fraction so a 11-of-6696 row doesn't look uncounted. */
function formatShareFr(value, total) {
  if (!total) return '0 %';
  const pct = (value / total) * 100;
  if (pct > 0 && pct < 0.1) return '<0,1 %';
  const digits = pct < 10 ? 1 : 0;
  return `${pct.toLocaleString('fr-FR', { minimumFractionDigits: digits, maximumFractionDigits: digits })} %`;
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
        const y = top + chartHeight - logShare(tick, maxValue) * chartHeight;
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
        const barHeight = Math.round(logShare(value, maxValue) * chartHeight);
        const y = top + chartHeight - barHeight;
        const isLatest = index === lastIndex;
        return (
          <g key={point.label ?? index}>
            {/* An empty day is empty — the old 2px stub with a "0" over it read as
                a tiny bar and made six quiet days look like six data points. Today
                is different: zero-so-far isn't the same claim as "nothing happened",
                so it gets a dashed placeholder instead of silence. */}
            {value > 0 ? (
              <>
                <rect
                  x={x}
                  y={y}
                  width={barWidth}
                  height={Math.max(barHeight, 3)}
                  rx="3"
                  fill={isLatest ? 'var(--primary)' : 'color-mix(in srgb, var(--primary) 62%, transparent)'}
                />
                <text x={x + barWidth / 2} y={Math.max(y - 6, 12)} textAnchor="middle" fontSize="11" fill="var(--text)">
                  {value}
                </text>
              </>
            ) : isLatest ? (
              <>
                <title>Aujourd'hui — pas encore de données</title>
                <rect
                  x={x}
                  y={top + chartHeight - 3}
                  width={barWidth}
                  height="3"
                  rx="1.5"
                  fill="none"
                  stroke="var(--muted)"
                  strokeDasharray="2 3"
                />
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
        // Log scale: a dominant category (e.g. auto-ignored bulk mail) would
        // otherwise flatten every other row to the 6px floor.
        const widthPx = Math.max(6, Math.round(logShare(value, maxValue) * barWidth));
        const shareLabel = formatShareFr(value, total);
        const label = workflowLabelFr(row);
        return (
          <g key={row.category ?? row.display_name ?? index}>
            <title>{`${label} — ${value} e-mail(s), ${shareLabel} de la période`}</title>
            <text x="8" y={y + 15} fontSize="12" fill="var(--text)">
              {label.length > 26 ? `${label.slice(0, 25)}…` : label}
            </text>
            <rect x={left} y={y + 2} width={barWidth} height="14" rx="4" fill="var(--surface-high)" />
            <rect x={left} y={y + 2} width={widthPx} height="14" rx="4" fill="var(--primary)" />
            <text x={width - 8} y={y + 13} textAnchor="end" fontSize="12" fill="var(--text)">{value}</text>
            <text x="8" y={y + 27} fontSize="10" fill="var(--muted)">{shareLabel}</text>
          </g>
        );
      })}
    </svg>
  );
}
