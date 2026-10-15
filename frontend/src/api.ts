// TradingOS V2 API client.
// Local Windows: direct localhost backend.
// Cloud Vercel: same-origin /api server-side gateway.
// No browser token storage and no fetch interception.

const isCloud =
  typeof window !== "undefined" &&
  window.location.hostname !== "localhost" &&
  window.location.hostname !== "127.0.0.1";

const localBase =
  import.meta.env.VITE_API_BASE_URL?.trim() ||
  "http://127.0.0.1:8765";

export const API_BASE_URL: string = isCloud
  ? "/api"
  : localBase.replace(/\/+$/, "");

export const API_ROOT: string = API_BASE_URL;

export function apiUrl(path: string): string {
  if (path.startsWith("http://") ||
      path.startsWith("https://")) {
    return path;
  }
  return `${API_BASE_URL}${path.startsWith("/") ? path : "/" + path}`;
}

// Compatibility exports; authentication is now cookie-based.
export function getAuthToken(): null {
  return null;
}

export function setAuthToken(_token: string | null): void {}

export function authHeaders(): Record<string, string> {
  return {};
}
