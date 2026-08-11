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

/**
 * A refused or failed *action*, as opposed to missing data.
 *
 * Reads had a banner and writes did not, so pressing a button a role is not
 * allowed to press did nothing at all: no error, no change, no explanation. That
 * is indistinguishable from a broken button, and it is why the console looked
 * like it had dead controls rather than controls the server declines.
 *
 * All actions share one entry: only the most recent matters, and stacking them
 * would bury the read failures the banner also carries.
 */
export const ACTION_FAILURE_ID = '["__action__"]';

export function recordActionFailure(error) {
  const message = error?.message || 'Erreur inconnue';
  const existing = failures.find((entry) => entry.id === ACTION_FAILURE_ID);
  if (existing && existing.message === message) return;
  failures = [
    ...failures.filter((entry) => entry.id !== ACTION_FAILURE_ID),
    { id: ACTION_FAILURE_ID, subject: 'action refusée', message },
  ];
  emit();
}

export function clearActionFailure() {
  if (!failures.some((entry) => entry.id === ACTION_FAILURE_ID)) return;
  failures = failures.filter((entry) => entry.id !== ACTION_FAILURE_ID);
  emit();
}

export function clearFailure(queryKey) {
  const id = JSON.stringify(queryKey);
  if (!failures.some((entry) => entry.id === id)) return;
  failures = failures.filter((entry) => entry.id !== id);
  emit();
}

/**
 * Drop failures recorded against an instance we are no longer looking at.
 *
 * Query keys carry the instance id, so switching instance — or correcting a
 * stale one — starts a *new* key. The retry that succeeds clears its own key
 * and leaves the old entry stranded in the banner with no way to remove it.
 */
export function clearFailuresForInstance(instanceId) {
  if (!instanceId) return;
  const needle = JSON.stringify(instanceId);
  const remaining = failures.filter((entry) => !entry.id.includes(needle));
  if (remaining.length === failures.length) return;
  failures = remaining;
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
