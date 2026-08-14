export function gatewayUrl(gatewayBase, path) {
  return `${gatewayBase.replace(/\/$/, '')}${path}`;
}

export function requestHeaders(token, instanceId, path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  // The selected instance is the default, but a caller acting on a specific
  // mailbox (the overview console tests each one in turn) may name its own.
  if (path.startsWith('/api/agent') && !headers['X-Agora-Agent-Instance'] && instanceId) {
    headers['X-Agora-Agent-Instance'] = instanceId;
  }
  return headers;
}

// The access token lives 15 minutes; the refresh cookie lives days. Without the
// rotation below every session died mid-action a quarter of an hour after login,
// which reads as "the app logged me out by itself".
let refreshInFlight = null;
let onTokenRefreshed = () => {};

/** AuthProvider registers the setter so a rotated token reaches React state. */
export function registerTokenListener(listener) {
  onTokenRefreshed = typeof listener === 'function' ? listener : () => {};
}

/** Rotates the refresh cookie for a new access token. Concurrent callers share
 * one request — a page firing six queries at once must not spend six rotations
 * (the gateway treats a reused refresh token as a replay and kills the family). */
export function refreshAccessToken(gatewayBase) {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(gatewayUrl(gatewayBase, '/auth/refresh'), {
          method: 'POST',
          credentials: 'include',
        });
        if (!response.ok) return '';
        const data = await response.json().catch(() => null);
        return data?.token || '';
      } catch (_error) {
        return '';
      }
    })();
    refreshInFlight.finally(() => { refreshInFlight = null; });
  }
  return refreshInFlight;
}

// A request the gateway abandons mid-inference never resolves here, and the
// blocking overlay above it waits forever. Nothing in this app should take
// longer than this, and a stated failure beats a spinner with no end.
const REQUEST_TIMEOUT_MS = Number(import.meta.env?.VITE_REQUEST_TIMEOUT_MS) || 300_000;

export class RequestTimeoutError extends Error {
  constructor() {
    super("Le serveur n’a pas répondu à temps. L’action est peut-être toujours en cours côté agent.");
    this.name = 'RequestTimeoutError';
  }
}

/** Fetch with a deadline, preserving any caller-supplied abort signal. */
async function fetchWithDeadline(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  if (options.signal) {
    options.signal.addEventListener('abort', () => controller.abort(), { once: true });
  }
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === 'AbortError') throw new RequestTimeoutError();
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** Runs `send(token)`, and on a 401 rotates the session once and replays it. */
async function withSessionRetry(gatewayBase, token, path, send) {
  const response = await send(token);
  if (response.status !== 401 || path.startsWith('/auth/')) return response;
  const refreshed = await refreshAccessToken(gatewayBase);
  if (!refreshed) return response;
  onTokenRefreshed(refreshed);
  return send(refreshed);
}

// FastAPI validation errors send `detail` as a list of {loc, msg, type}
// objects (or, less often, a single object) rather than a string — surface
// something readable instead of the default "[object Object]" stringification.
function formatDetail(detail) {
  if (!detail || typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : null;
        return field ? `${field} : ${item.msg}` : item?.msg;
      })
      .filter(Boolean)
      .join(' ; ') || null;
  }
  return detail.msg || JSON.stringify(detail);
}

// Reverse proxies (nginx, cloud load balancers) answer outages with an HTML
// error page, not JSON. Left unhandled, that raw markup — full of literal
// "<html>" tags and HTML comments — ends up as the error message shown to the
// user, which is neither readable nor something a non-technical person can
// act on.
function friendlyGatewayMessage(status) {
  if (status === 502 || status === 504) {
    return "Le service est momentanément indisponible. Réessayez dans quelques instants.";
  }
  if (status === 503) {
    return "Le service redémarre. Réessayez dans quelques instants.";
  }
  return null;
}

export async function responseError(response, signOut) {
  const text = await response.text();
  let message = text || `${response.status} ${response.statusText}`;
  try {
    const parsed = JSON.parse(text);
    message = parsed.error || formatDetail(parsed.detail) || message;
  } catch (_error) {
    message = friendlyGatewayMessage(response.status) || message;
  }
  if (response.status === 401 || message === 'unauthorized') {
    if (typeof signOut === 'function') signOut();
    return new Error("Session expiree. Reconnectez-vous.");
  }
  if (response.status === 403) {
    return new Error('Interdit pour ce rôle.');
  }
  if (response.status === 410) {
    return new Error("Cette exécution a expiré : son état n’existe plus. Elle a été retirée de la file.");
  }
  return new Error(message);
}

// We pass the auth details so this can be used outside React context (or inside hooks)
export async function api(gatewayBase, token, instanceId, signOut, path, options = {}) {
  const response = await withSessionRetry(gatewayBase, token, path, (activeToken) => fetchWithDeadline(
    gatewayUrl(gatewayBase, path),
    { ...options, headers: requestHeaders(activeToken, instanceId, path, options), credentials: 'include' },
  ));

  if (!response.ok) throw await responseError(response, signOut);
  
  const contentType = response.headers.get('content-type') || '';
  return contentType.includes('application/json') ? response.json() : response.text();
}

// Multipart uploads (images) can't go through api()'s JSON-only Content-Type
// default — build the FormData/headers by hand.
export async function apiUpload(gatewayBase, token, instanceId, signOut, path, file) {
  const formData = new FormData();
  formData.append('file', file, file.name);
  const response = await withSessionRetry(gatewayBase, token, path, (activeToken) => {
    const headers = { Authorization: `Bearer ${activeToken}` };
    if (path.startsWith('/api/agent') && instanceId) headers['X-Agora-Agent-Instance'] = instanceId;
    return fetchWithDeadline(gatewayUrl(gatewayBase, path), { method: 'POST', headers, body: formData, credentials: 'include' });
  });
  if (!response.ok) throw await responseError(response, signOut);
  return response.json();
}

// <img src> can't carry the Authorization header, so authenticated images
// (the stored signature) are fetched as a blob and shown via an object URL.
export async function apiBlob(gatewayBase, token, instanceId, signOut, path) {
  const response = await withSessionRetry(gatewayBase, token, path, (activeToken) => fetch(
    gatewayUrl(gatewayBase, path),
    { headers: requestHeaders(activeToken, instanceId, path), credentials: 'include' },
  ));
  if (!response.ok) return null;
  return response.blob();
}

export async function streamApi(gatewayBase, token, instanceId, signOut, path, options = {}, onEvent = () => {}) {
  const response = await withSessionRetry(gatewayBase, token, path, (activeToken) => fetchWithDeadline(
    gatewayUrl(gatewayBase, path),
    { ...options, headers: requestHeaders(activeToken, instanceId, path, options), credentials: 'include' },
  ));

  if (!response.ok) throw await responseError(response, signOut);
  if (!response.body) {
    onEvent({ event: 'message', data: await response.text() });
    return;
  }
  
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const emitBlock = (block) => {
    if (!block.trim()) return;
    let event = 'message';
    const dataLines = [];
    block.split(/\r?\n/).forEach((line) => {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    });
    const raw = dataLines.join('\n');
    let data = raw;
    try {
      data = JSON.parse(raw);
    } catch (_error) {}
    onEvent({ event, data });
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary = buffer.search(/\r?\n\r?\n/);
    while (boundary !== -1) {
      const separator = buffer.slice(boundary).startsWith('\r\n\r\n') ? 4 : 2;
      emitBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + separator);
      boundary = buffer.search(/\r?\n\r?\n/);
    }
    if (done) break;
  }
  if (buffer.trim()) emitBlock(buffer);
}
