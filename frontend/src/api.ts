// TradingOS V2 - Centralized API Client & Browser Authentication Configuration
// Supports:
// 1. Environment-driven backend URL (VITE_API_BASE_URL) with local fallback (http://127.0.0.1:8765)
// 2. Backward-compatible API_ROOT and API_BASE_URL exports
// 3. Secure token-based client authentication stored in localStorage
// 4. URL query token capture (?token=... or ?auth=...) with history state sanitization
// 5. Automatic header injection for private cloud API requests
//
// NOTE: VITE_ environment variables are publicly visible in client-side bundles.
// NEVER put Bybit API keys, Bybit secrets, database URLs, or private tokens here.

const rawBaseUrl = import.meta.env.VITE_API_BASE_URL;

export const API_BASE_URL: string =
  typeof rawBaseUrl === "string" && rawBaseUrl.trim() !== ""
    ? rawBaseUrl.trim().replace(/\/+$/, "")
    : "http://127.0.0.1:8765";

export const API_ROOT: string = API_BASE_URL;

const STORAGE_KEY = "tradingos_auth_token";

/**
 * Retrieve the active browser session authentication token.
 */
export function getAuthToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

/**
 * Update or clear the browser authentication token.
 */
export function setAuthToken(token: string | null): void {
  try {
    if (token && token.trim()) {
      localStorage.setItem(STORAGE_KEY, token.trim());
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {}
}

// Auto-detect and store authentication token passed via URL parameter (?token=... or ?auth=...)
if (typeof window !== "undefined") {
  try {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token") || params.get("auth");
    if (urlToken && urlToken.trim()) {
      setAuthToken(urlToken.trim());
      // Clean query parameter from browser address bar without reload
      params.delete("token");
      params.delete("auth");
      const newQuery = params.toString() ? `?${params.toString()}` : "";
      window.history.replaceState(null, "", `${window.location.pathname}${newQuery}${window.location.hash}`);
    }
  } catch {}
}

/**
 * Return authorization headers dictionary if a token is configured.
 */
export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  if (!token) return {};
  return {
    "Authorization": `Bearer ${token}`,
    "X-TradingOS-Auth": token,
  };
}

/**
 * Helper to construct a full API URL given a relative endpoint path.
 */
export function apiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${cleanPath}`;
}

// Automatic global fetch wrapper to attach authentication headers to API_ROOT calls
if (typeof window !== "undefined" && window.fetch) {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    try {
      const urlStr =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.toString()
          : (input as Request).url;

      const token = getAuthToken();
      if (token && urlStr.startsWith(API_BASE_URL)) {
        const modifiedInit = { ...(init || {}) };
        const headers = new Headers(modifiedInit.headers || {});
        if (!headers.has("Authorization")) {
          headers.set("Authorization", `Bearer ${token}`);
        }
        if (!headers.has("X-TradingOS-Auth")) {
          headers.set("X-TradingOS-Auth", token);
        }
        modifiedInit.headers = headers;
        return nativeFetch(input, modifiedInit);
      }
    } catch {
      // Fall back to native fetch on any interception anomaly
    }
    return nativeFetch(input, init);
  };
}
