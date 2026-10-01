/** API base URL and small fetch helpers. */

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000';

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

/** Absolute URL for a file served by the API's storage adapter. */
export function fileUrl(projectId: string, relativePath: string): string {
  return apiUrl(`/files/projects/${projectId}/scene/${relativePath}`);
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path));
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
