import { passwordStrength } from '../../utils/passwordStrength';

/**
 * Live readout under a new-password field.
 *
 * Announced politely rather than assertively: it updates on every keystroke, and
 * an assertive live region would interrupt a screen-reader user mid-word.
 */
export function PasswordStrength({ value, id }) {
  const { empty, level, score, hint } = passwordStrength(value);

  return (
    <div className="password-strength" id={id} aria-live="polite">
      <div className="password-strength-track" aria-hidden="true">
        {[0, 1, 2].map((step) => (
          <span
            key={step}
            className={
              'password-strength-step'
              + (!empty && step <= score ? ` is-${level}` : '')
            }
          />
        ))}
      </div>
      <span className="password-strength-label">
        {empty ? 'Force du mot de passe' : `Force : ${level}`}
      </span>
      {hint ? <small className="password-strength-hint">{hint}</small> : null}
    </div>
  );
}
