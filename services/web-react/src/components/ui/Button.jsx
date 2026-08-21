/**
 * The only button in the app.
 *
 * Every page used to write its own `<button className="primary">` with an
 * inline `<span className="material-symbols-outlined">`, which is why sizes,
 * icon spacing and loading behaviour drifted apart page by page. Two rules
 * matter more than the styling:
 *
 *  - **A button that starts something says so.** `loading` swaps the icon for a
 *    spinner, disables the control and, when given, shows `loadingLabel`.
 *    Without it a slow action reads as a dead button and gets clicked twice.
 *  - **Size carries rank.** `sm` above a table, default inside a form, `lg` for
 *    the action that commits the page.
 */
export function Button({
  children,
  icon,
  variant,          // 'primary' | 'danger' | 'ghost' | 'success' | 'warning'
  size = 'md',      // 'sm' | 'md' | 'lg'
  loading = false,
  loadingLabel,
  disabled = false,
  className = '',
  type = 'button',
  ...rest
}) {
  const classes = [
    variant || '',
    size !== 'md' ? `btn-${size}` : '',
    loading ? 'is-loading' : '',
    className,
  ].filter(Boolean).join(' ');

  const shownIcon = loading ? 'progress_activity' : icon;

  return (
    <button
      type={type}
      className={classes}
      // A loading button must not be clickable again: several actions here are
      // not idempotent (a send, a campaign, a retry).
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {shownIcon ? (
        <span
          className={'material-symbols-outlined' + (loading ? ' spin' : '')}
          aria-hidden="true"
        >
          {shownIcon}
        </span>
      ) : null}
      <span>{loading && loadingLabel ? loadingLabel : children}</span>
    </button>
  );
}

/**
 * Square, icon-only. Always needs a label — an icon alone tells a screen reader
 * nothing, and it is also the tooltip a sighted user needs on an unlabelled
 * glyph.
 */
export function IconButton({
  icon,
  label,
  variant,
  size = 'md',
  loading = false,
  disabled = false,
  className = '',
  ...rest
}) {
  const classes = [
    'icon-button',
    variant || '',
    size !== 'md' ? `btn-${size}` : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <button
      type="button"
      className={classes}
      title={label}
      aria-label={label}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      <span
        className={'material-symbols-outlined' + (loading ? ' spin' : '')}
        aria-hidden="true"
      >
        {loading ? 'progress_activity' : icon}
      </span>
    </button>
  );
}
