import { scaleSymlog } from 'd3-scale';
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
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

/** A symlog scale behaves like log1p above ~1 but stays finite (and linear)
 * through zero, which plain d3 log scales can't do — half of any given
 * period here is legitimately zero (quiet days, rare categories). Recharts
 * calls .copy().domain(...).range(...) on whatever scale function it's given
 * (see combineConfiguredScaleInternal), so this only needs to be a valid d3
 * scale factory — domain/range come from the axis's own domain/range props. */
function logScale() {
  return scaleSymlog().constant(1);
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

function VolumeTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <strong>{point.label}</strong>
      <span>{point.count > 0 ? `${point.count} e-mail(s)` : 'Aucune donnée'}</span>
    </div>
  );
}

export function VerticalBarChart({ points, emptyLabel }) {
  const rows = (points || [])
    .filter((point) => Number(point.count || 0) >= 0)
    .map((point) => ({ label: point.label || '', count: Number(point.count || 0) }));
  const maxValue = Math.max(0, ...rows.map((row) => row.count));
  if (!rows.length || maxValue === 0) return <div className="empty">{emptyLabel}</div>;

  const lastIndex = rows.length - 1;
  const ticks = axisTicks(maxValue);

  return (
    <ResponsiveContainer width="100%" height={220} aria-label="Volume traité sur la période">
      <BarChart data={rows} margin={{ top: 18, right: 12, bottom: 4, left: 0 }} barCategoryGap="20%">
        <XAxis
          dataKey="label"
          axisLine={{ stroke: 'var(--outline)' }}
          tickLine={false}
          interval={rows.length > 10 ? 1 : 0}
          tick={({ x, y, payload, index }) => (
            <text
              x={x}
              y={y + 14}
              textAnchor="middle"
              fontSize="11"
              fill={index === lastIndex ? 'var(--text)' : 'var(--muted)'}
            >
              {payload.value}
            </text>
          )}
        />
        <YAxis
          scale={logScale()}
          domain={[0, maxValue]}
          ticks={ticks}
          axisLine={false}
          tickLine={false}
          tick={{ fontSize: 10, fill: 'var(--muted)' }}
          width={28}
        />
        <Tooltip content={<VolumeTooltip />} cursor={{ fill: 'var(--surface-high)' }} />
        <Bar dataKey="count" radius={[3, 3, 0, 0]} maxBarSize={48} isAnimationActive={false}>
          {rows.map((row, index) => (
            <Cell
              key={row.label || index}
              // An empty day is empty — a 2px stub with a "0" over it read as a
              // tiny bar and made six quiet days look like six data points. Today
              // is different: zero-so-far isn't the same claim as "nothing
              // happened", so it gets a dashed outline instead of nothing at all.
              fill={
                row.count > 0
                  ? index === lastIndex
                    ? 'var(--primary)'
                    : 'color-mix(in srgb, var(--primary) 62%, transparent)'
                  : 'transparent'
              }
              stroke={row.count === 0 && index === lastIndex ? 'var(--muted)' : undefined}
              strokeDasharray={row.count === 0 && index === lastIndex ? '2 3' : undefined}
            />
          ))}
          <LabelList
            dataKey="count"
            position="top"
            content={({ x, y, width, value, index }) =>
              value > 0 ? (
                <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize="11" fill="var(--text)">
                  {value}
                </text>
              ) : null
            }
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function CategoryTick({ x, y, payload, labelByKey }) {
  const meta = labelByKey.get(payload.value);
  if (!meta) return null;
  return (
    <g transform={`translate(${x},${y})`}>
      <text x={0} y={-3} textAnchor="end" fontSize="12" fill="var(--text)">
        {meta.label.length > 26 ? `${meta.label.slice(0, 25)}…` : meta.label}
      </text>
      <text x={0} y={11} textAnchor="end" fontSize="10" fill="var(--muted)">
        {meta.shareLabel}
      </text>
    </g>
  );
}

export function HorizontalBarChart({ rows, emptyLabel }) {
  const total = (rows || []).reduce((sum, row) => sum + Number(row.count || 0), 0);
  const items = (rows || []).slice(0, 5).map((row, index) => {
    const count = Number(row.count || 0);
    const key = row.category ?? row.display_name ?? String(index);
    return { key, count, label: workflowLabelFr(row), shareLabel: formatShareFr(count, total) };
  });
  const maxValue = Math.max(0, ...items.map((item) => item.count));
  if (!items.length || maxValue === 0) return <div className="empty">{emptyLabel}</div>;

  const labelByKey = new Map(items.map((item) => [item.key, item]));

  return (
    <ResponsiveContainer width="100%" height={items.length * 32 + 16} aria-label="Cas métier les plus actifs sur la période">
      <BarChart
        data={items}
        layout="vertical"
        margin={{ top: 4, right: 44, bottom: 4, left: 0 }}
        barCategoryGap="24%"
      >
        <XAxis type="number" scale={logScale()} domain={[0, maxValue]} hide />
        <YAxis
          type="category"
          dataKey="key"
          width={176}
          axisLine={false}
          tickLine={false}
          tick={(props) => <CategoryTick {...props} labelByKey={labelByKey} />}
        />
        <Tooltip
          cursor={{ fill: 'var(--surface-high)' }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const item = payload[0].payload;
            return (
              <div className="chart-tooltip">
                <strong>{item.label}</strong>
                <span>{item.count} e-mail(s), {item.shareLabel} de la période</span>
              </div>
            );
          }}
        />
        <Bar
          dataKey="count"
          fill="var(--primary)"
          background={{ fill: 'var(--surface-high)', radius: 4 }}
          radius={4}
          barSize={14}
          isAnimationActive={false}
        >
          <LabelList
            dataKey="count"
            position="right"
            content={({ x, y, height, value }) => (
              <text x={x + 8} y={y + height / 2 + 4} fontSize="12" fill="var(--text)">
                {value}
              </text>
            )}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
