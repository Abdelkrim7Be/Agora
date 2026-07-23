import { useI18n } from '../../contexts/I18nContext';
import { useStatus } from '../../contexts/StatusContext';

export function PageHeading({ view, actions }) {
  const { t } = useI18n();
  const { message, kind } = useStatus();

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
      <div className="heading-actions">
        {actions}
        <span className={`status ${kind}`.trim()} role="status">{message}</span>
      </div>
    </section>
  );
}
