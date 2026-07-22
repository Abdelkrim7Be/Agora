export function decodeJwtPayload(token) {
  if (!token) return {};
  try {
    const encoded = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
  } catch (_error) {
    return {};
  }
}

export function decodeJwtRole(token) {
  return String(decodeJwtPayload(token).role || "").toLowerCase();
}

export function currentUsername(token) {
  return String(decodeJwtPayload(token).sub || "");
}
