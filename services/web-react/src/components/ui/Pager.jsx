export function Pager({ page, hasMore, onPrev, onNext, countLabel, className = '' }) {
  return (
    <span className={`pager ${className}`.trim()}>
      <button type="button" disabled={page === 0} onClick={onPrev}>
        <span className="material-symbols-outlined" aria-hidden="true">chevron_left</span>
        <span>Précédent</span>
      </button>
      <span className="counter">Page {page + 1}</span>
      {countLabel ? <span className="counter">{countLabel}</span> : null}
      <button type="button" disabled={!hasMore} onClick={onNext}>
        <span>Suivant</span>
        <span className="material-symbols-outlined" aria-hidden="true">chevron_right</span>
      </button>
    </span>
  );
}
