import { PAGE_SIZE_OPTIONS } from '../../hooks/usePagination';

/** Page numbers around the current one, always including the first and last. */
function pageWindow(page, pageCount, span = 2) {
  const numbers = new Set([0, pageCount - 1]);
  for (let offset = -span; offset <= span; offset += 1) {
    const candidate = page + offset;
    if (candidate >= 0 && candidate < pageCount) numbers.add(candidate);
  }
  const ordered = [...numbers].sort((a, b) => a - b);
  const withGaps = [];
  ordered.forEach((value, index) => {
    if (index > 0 && value - ordered[index - 1] > 1) withGaps.push('gap');
    withGaps.push(value);
  });
  return withGaps;
}

/**
 * Pagination for a table.
 *
 * `pageCount` is known when the whole list is in memory; the audit trail pages
 * server-side and only knows whether another page exists, so it passes
 * `hasMore` instead and the numbers collapse to prev/next.
 */
export function TablePager({
  page,
  pageCount,
  total,
  size,
  onPage,
  onSize,
  hasMore,
  unit = 'lignes',
  className = '',
}) {
  const knowsTotal = Number.isFinite(pageCount);
  const canPrev = page > 0;
  const canNext = knowsTotal ? page < pageCount - 1 : Boolean(hasMore);
  const firstRow = total === 0 ? 0 : page * size + 1;
  const lastRow = knowsTotal ? Math.min(total, page * size + size) : null;

  return (
    <nav className={`table-pager ${className}`.trim()} aria-label="Pagination">
      {Number.isFinite(total) ? (
        <span className="table-pager-range">
          {total === 0 ? `Aucune ligne` : `${firstRow}–${lastRow} sur ${total} ${unit}`}
        </span>
      ) : null}

      <span className="table-pager-controls">
        <button type="button" disabled={!canPrev} onClick={() => onPage(page - 1)} aria-label="Page précédente">
          <span className="material-symbols-outlined" aria-hidden="true">chevron_left</span>
        </button>

        {knowsTotal ? pageWindow(page, pageCount).map((value, index) => (
          value === 'gap' ? (
            <span className="table-pager-gap" key={`gap-${index}`} aria-hidden="true">…</span>
          ) : (
            <button
              type="button"
              key={value}
              className={value === page ? 'primary' : ''}
              aria-current={value === page ? 'page' : undefined}
              onClick={() => onPage(value)}
            >
              {value + 1}
            </button>
          )
        )) : <span className="counter">Page {page + 1}</span>}

        <button type="button" disabled={!canNext} onClick={() => onPage(page + 1)} aria-label="Page suivante">
          <span className="material-symbols-outlined" aria-hidden="true">chevron_right</span>
        </button>
      </span>

      {onSize ? (
        <label className="table-pager-size">
          <span>Par page</span>
          <select value={size} onChange={(event) => onSize(Number(event.target.value))}>
            {PAGE_SIZE_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        </label>
      ) : null}
    </nav>
  );
}
