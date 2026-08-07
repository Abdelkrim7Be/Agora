/**
 * A hint, not a rule.
 *
 * The server accepts any password of 12 characters or more
 * (`UserController.rejectWeakPassword`) and nothing here changes that. This
 * exists so someone typing `motdepasse12` sees that it is weak *before*
 * committing to it — not so the form can refuse it on a criterion we never
 * documented.
 *
 * Deliberately not a real strength estimator: zxcvbn is ~400 kB and would be
 * the largest thing in the bundle. This scores length and character variety,
 * which is honest about being a rough guide, and knocks anything built on an
 * obvious base word straight down to the bottom.
 */

// Bases that appear in every leaked-password list. A password containing one of
// these is guessable whatever else is bolted onto it, so it can never score
// above "faible".
const COMMON_BASES = [
  'password', 'motdepasse', 'azerty', 'qwerty', 'admin', 'welcome', 'bienvenue',
  'letmein', 'iloveyou', 'soleil', 'dragon', 'monkey', 'football', 'agora',
  'agora', 'changeme', 'secret', 'default',
];

export const STRENGTH_LEVELS = ['faible', 'moyen', 'fort'];

/**
 * @returns {{ level: 'faible'|'moyen'|'fort', score: 0|1|2, hint: string }}
 *          plus `empty: true` when there is nothing to judge yet.
 */
export function passwordStrength(password) {
  const value = String(password || '');
  if (!value) {
    return { empty: true, level: 'faible', score: 0, hint: '' };
  }

  const lowered = value.toLowerCase();
  const base = COMMON_BASES.find((word) => lowered.includes(word));
  if (base) {
    return {
      empty: false,
      level: 'faible',
      score: 0,
      hint: `Contient « ${base} », un mot présent dans toutes les listes de mots de passe courants.`,
    };
  }

  if (value.length < 12) {
    return {
      empty: false,
      level: 'faible',
      score: 0,
      hint: `Encore ${12 - value.length} caractère${12 - value.length > 1 ? 's' : ''} avant le minimum accepté.`,
    };
  }

  // Only counted past the minimum length, so variety cannot make a short
  // password look acceptable.
  const variety = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/]
    .filter((pattern) => pattern.test(value)).length;

  // A long passphrase is strong without punctuation, which is why length alone
  // can reach the top: "le chat dort sur le clavier" beats "P@ssw0rd!2024".
  if (value.length >= 20 || (value.length >= 16 && variety >= 3) || variety === 4) {
    return { empty: false, level: 'fort', score: 2, hint: '' };
  }

  if (variety >= 2) {
    return {
      empty: false,
      level: 'moyen',
      score: 1,
      hint: 'Une phrase plus longue protège mieux qu’un mot court avec des symboles.',
    };
  }

  return {
    empty: false,
    level: 'faible',
    score: 0,
    hint: 'Un seul type de caractère. Allongez-le, ou mélangez majuscules, chiffres ou ponctuation.',
  };
}
