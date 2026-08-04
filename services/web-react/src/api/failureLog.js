/**
 * Every page renders `query.data || []`, so a request that fails looks exactly
 * like a page with nothing on it: "0 €" of cost, "no failed actions", "no
 * pending approvals". That is the worst possible failure mode for a supervision
 * console — it reports good news it never actually confirmed.
 *
 * Rather than thread error handling through ~30 pages, the query cache reports
 * failures here and the layouts render one banner. The data path is untouched.
 */

const listeners = new Set();
let failures = [];

function emit() {
  const snapshot = failures;
  listeners.forEach((listener) => listener(snapshot));
}

/** Human-readable label for a query key, so the banner can name what is missing. */
function describe(queryKey) {
  const head = Array.isArray(queryKey) ? queryKey[0] : queryKey;
  return typeof head === 'string' ? head : 'données';
}

export function recordFailure(queryKey, error) {
  const id = JSON.stringify(queryKey);
  const message = error?.message || 'Erreur inconnue';
  const existing = failures.find((entry) => entry.id === id);
  if (existing && existing.message === message) return;
  failures = [...failures.filter((entry) => entry.id !== id), { id, subject: describe(queryKey), message }];
  emit();
}

export function clearFailure(queryKey) {
  const id = JSON.stringify(queryKey);
  if (!failures.some((entry) => entry.id === id)) return;
  failures = failures.filter((entry) => entry.id !== id);
  emit();
}

export function clearAllFailures() {
  if (failures.length === 0) return;
  failures = [];
  emit();
}

export function subscribeToFailures(listener) {
  listeners.add(listener);
  listener(failures);
  return () => listeners.delete(listener);
}
