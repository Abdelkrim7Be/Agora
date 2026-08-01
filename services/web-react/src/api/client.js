export function gatewayUrl(gatewayBase, path) {
  return `${gatewayBase.replace(/\/$/, '')}${path}`;
}

export function requestHeaders(token, instanceId, path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  if (path.startsWith('/api/agent') && instanceId) {
    headers['X-Agora-Agent-Instance'] = instanceId;
  }
  return headers;
}

export async function responseError(response, signOut) {
  const text = await response.text();
  let message = text || `${response.status} ${response.statusText}`;
  try {
    const parsed = JSON.parse(text);
    message = parsed.error || parsed.detail || message;
  } catch (_error) {
    // Keep raw
  }
  if (response.status === 401 || message === 'unauthorized') {
    if (typeof signOut === 'function') signOut();
    return new Error("Session expiree. Reconnectez-vous.");
  }
  if (response.status === 403) {
    return new Error('Interdit pour ce rôle.');
  }
  return new Error(message);
}

// We pass the auth details so this can be used outside React context (or inside hooks)
export async function api(gatewayBase, token, instanceId, signOut, path, options = {}) {
  const headers = requestHeaders(token, instanceId, path, options);
  const response = await fetch(gatewayUrl(gatewayBase, path), { ...options, headers });
  
  if (!response.ok) throw await responseError(response, signOut);
  
  const contentType = response.headers.get('content-type') || '';
  return contentType.includes('application/json') ? response.json() : response.text();
}

// Multipart uploads (images) can't go through api()'s JSON-only Content-Type
// default — build the FormData/headers by hand.
export async function apiUpload(gatewayBase, token, instanceId, signOut, path, file) {
  const formData = new FormData();
  formData.append('file', file, file.name);
  const headers = { Authorization: `Bearer ${token}` };
  if (path.startsWith('/api/agent') && instanceId) headers['X-Agora-Agent-Instance'] = instanceId;
  const response = await fetch(gatewayUrl(gatewayBase, path), { method: 'POST', headers, body: formData });
  if (!response.ok) throw await responseError(response, signOut);
  return response.json();
}

// <img src> can't carry the Authorization header, so authenticated images
// (the stored signature) are fetched as a blob and shown via an object URL.
export async function apiBlob(gatewayBase, token, instanceId, signOut, path) {
  const headers = requestHeaders(token, instanceId, path);
  const response = await fetch(gatewayUrl(gatewayBase, path), { headers });
  if (!response.ok) return null;
  return response.blob();
}

export async function streamApi(gatewayBase, token, instanceId, signOut, path, options = {}, onEvent = () => {}) {
  const headers = requestHeaders(token, instanceId, path, options);
  const response = await fetch(gatewayUrl(gatewayBase, path), { ...options, headers });
  
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
