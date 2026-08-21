/**
 * Label + control + hint, in one shape.
 *
 * Pages wrote `<label>Nom<input/></label>` in a dozen slightly different ways —
 * some with a `<span>`, some without, some with a `<small>` hint underneath,
 * some with the hint above. That is why no two forms in the app lined up.
 *
 * The label always wraps its control, so clicking the text focuses the field
 * without needing an id/htmlFor pair that half the call sites forgot.
 */
export function Field({ label, hint, error, children, className = '' }) {
  return (
    <label className={['field', error ? 'has-error' : '', className].filter(Boolean).join(' ')}>
      {label ? <span className="field-label">{label}</span> : null}
      {children}
      {error ? (
        <small className="field-error" role="alert">{error}</small>
      ) : hint ? (
        <small className="field-hint">{hint}</small>
      ) : null}
    </label>
  );
}

/**
 * A native select, styled once.
 *
 * Kept native on purpose: a custom listbox has to reimplement keyboard
 * navigation, typeahead and mobile pickers, and every hand-rolled one in a
 * console like this ends up worse than the browser's. The chevron and sizing
 * come from CSS on `select`, so this only exists to carry the size class and
 * keep call sites consistent.
 */
export function Select({ size = 'md', className = '', children, ...rest }) {
  const classes = [size !== 'md' ? `control-${size}` : '', className].filter(Boolean).join(' ');
  return <select className={classes} {...rest}>{children}</select>;
}

export function TextInput({ size = 'md', className = '', ...rest }) {
  const classes = [size !== 'md' ? `control-${size}` : '', className].filter(Boolean).join(' ');
  return <input className={classes} {...rest} />;
}
