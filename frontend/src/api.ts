// Public API base URL only. Never place secrets in VITE_ environment variables.
export const API_ROOT = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8765").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_ROOT}${path.startsWith("/") ? path : `/${path}`}`;
}
