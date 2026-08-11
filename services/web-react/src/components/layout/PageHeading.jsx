import { useI18n } from '../../contexts/I18nContext';

// The status line here used to be the only feedback an action gave, so it had
// to stay put and be readable. Now every setStatus() call also raises a
// Sonner toast — this pill just said the same thing a second time, pinned
// to the far edge of the header where it read as misplaced rather than
// redundant.
export function PageHeading({ view, actions }) {
  const { t } = useI18n();

  return (
    <section className="page-heading" aria-labelledby="page-title">
      <div>
        <div className="eyebrow">
          <span className="material-symbols-outlined" aria-hidden="true">security</span>
          <span>{t(`view.${view}.kicker`)}</span>
        </div>
        <h1 id="page-title">{t(`view.${view}.title`)}</h1>
        <p>{t(`view.${view}.description`)}</p>
      </div>
      {actions ? <div className="heading-actions">{actions}</div> : null}
    </section>
  );
}
