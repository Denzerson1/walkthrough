/** API base URL and small fetch helpers. */

/**
 * The GitHub Pages build (`vite build --mode pages`) has no server. Its GET
 * endpoints are JSON files beside the page and its scene files sit under
 * files/, both written by scripts/export_site.py; nothing is posted anywhere.
 */
export const IS_STATIC = import.meta.env.MODE === 'pages';

const BASE = IS_STATIC
  ? import.meta.env.BASE_URL.replace(/\/$/, '')
  : ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000');

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

/** Absolute URL for a file served by the API's storage adapter. */
export function fileUrl(projectId: string, relativePath: string): string {
  return apiUrl(`/files/projects/${projectId}/scene/${relativePath}`);
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(IS_STATIC ? `${path}.json` : path));
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

/** Analytics events are best-effort: never let one break the viewer. */
export function postEvent(
  projectId: string,
  body: {
    session_id: string;
    event: string;
    room_id?: string;
    value?: string;
    duration_ms?: number;
  },
): void {
  if (IS_STATIC) return;
  void fetch(apiUrl(`/api/projects/${projectId}/events`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    keepalive: true,
  }).catch(() => undefined);
}

export function sessionId(): string {
  const key = 'wt_session';
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const fresh = Math.random().toString(36).slice(2, 14);
    sessionStorage.setItem(key, fresh);
    return fresh;
  } catch {
    return 'anonymous';
  }
}
