/**
 * Internal editor at /edit/:projectId (M2).
 *
 * Protected by the shared EDITOR_PASSWORD. Edits the manifest: waypoints,
 * room names and types, floor polygons, the scale reference and privacy
 * regions.
 */

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FloorPlan } from '../components/FloorPlan';
import { apiUrl, getJson } from '../lib/api';
import type { Manifest, Room, RoomType } from '../lib/manifest';
import { polygonArea } from '../lib/geometry';

const ROOM_TYPES: RoomType[] = [
  'living', 'bedroom', 'kitchen', 'dining', 'bathroom', 'hallway', 'office', 'other',
];

export function Editor() {
  const { projectId = '' } = useParams();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState<string | null>(null);

  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [selectedRoomId, setSelectedRoomId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    fetch(apiUrl('/api/editor/session'), { credentials: 'include' })
      .then((r) => setAuthed(r.ok))
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (!authed) return;
    getJson<Manifest>(`/api/projects/${projectId}/manifest`)
      .then((m) => {
        setManifest(m);
        setSelectedRoomId(m.rooms[0]?.id ?? null);
      })
      .catch(() => setStatus(`Could not load "${projectId}".`));
  }, [authed, projectId]);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setLoginError(null);
    const response = await fetch(apiUrl('/api/editor/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ password }),
    });
    if (response.ok) {
      setAuthed(true);
    } else {
      const body = await response.json().catch(() => ({ detail: 'Login failed' }));
      setLoginError(body.detail ?? 'Login failed');
    }
  }

  const patchRoom = useCallback(
    (roomId: string, patch: Partial<Room>) => {
      setManifest((m) =>
        m
          ? { ...m, rooms: m.rooms.map((r) => (r.id === roomId ? { ...r, ...patch } : r)) }
          : m,
      );
      setDirty(true);
    },
    [],
  );

  async function save() {
    if (!manifest) return;
    setStatus('Saving…');
    const response = await fetch(apiUrl(`/api/projects/${projectId}/manifest`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(manifest),
    });
    if (response.ok) {
      setStatus('Saved');
      setDirty(false);
      setTimeout(() => setStatus(null), 2000);
    } else {
      const body = await response.json().catch(() => ({ detail: 'Save failed' }));
      setStatus(`Save failed: ${body.detail}`);
    }
  }

  if (authed === null) {
    return <main className="p-8 text-[13px] opacity-60">Checking your session…</main>;
  }

  if (!authed) {
    return (
      <main className="flex h-full items-center justify-center p-6">
        <form onSubmit={login} className="w-full max-w-[22rem]">
          <h1 className="text-[18px] font-semibold tracking-tight">Editor</h1>
          <p className="mt-1.5 text-[13px] opacity-65">
            This area edits the published apartment.
          </p>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Editor password"
            aria-label="Editor password"
            autoComplete="current-password"
            className="rule mt-5 w-full border bg-transparent px-3 py-2.5 text-[14px]"
          />
          {loginError && (
            <p
              className="mt-2 border-l-2 pl-3 text-[13px]"
              style={{ borderColor: 'var(--staged)' }}
              role="alert"
            >
              {loginError}
            </p>
          )}
          <button
            type="submit"
            className="mt-4 w-full bg-[var(--paper)] px-4 py-2.5 text-[14px] font-semibold text-[#14171C]"
          >
            Open editor
          </button>
        </form>
      </main>
    );
  }

  if (!manifest) {
    return <main className="p-8 text-[13px] opacity-60">{status ?? 'Loading…'}</main>;
  }

  const room = manifest.rooms.find((r) => r.id === selectedRoomId) ?? null;

  return (
    <main className="mx-auto max-w-[70rem] px-5 py-8">
      <header className="rule flex flex-wrap items-baseline justify-between gap-3 border-b pb-4">
        <div>
          <h1 className="text-[18px] font-semibold tracking-tight">{manifest.name}</h1>
          <p className="measure text-[12px] opacity-55">
            {projectId} · {manifest.rooms.length} rooms · {manifest.units}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {status && <span className="text-[12px] opacity-70">{status}</span>}
          <button
            type="button"
            onClick={save}
            disabled={!dirty}
            className="bg-[var(--paper)] px-4 py-2 text-[13px] font-semibold text-[#14171C] disabled:opacity-35"
          >
            Save manifest
          </button>
        </div>
      </header>

      <div className="mt-6 grid gap-8 md:grid-cols-[auto,1fr]">
        <div className="panel rule h-fit rounded-sm border p-3">
          <FloorPlan
            manifest={manifest}
            activeRoomId={selectedRoomId}
            stagedRoomIds={new Set()}
            onSelectRoom={setSelectedRoomId}
            size={220}
          />
        </div>

        <div>
          <div className="scroll-x flex gap-1">
            {manifest.rooms.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setSelectedRoomId(r.id)}
                aria-pressed={r.id === selectedRoomId}
                className={[
                  'shrink-0 px-3 py-1.5 text-[12px]',
                  r.id === selectedRoomId ? 'bg-[var(--blueprint)]' : 'rule border opacity-70',
                ].join(' ')}
              >
                {r.name}
              </button>
            ))}
          </div>

          {room && (
            <div className="mt-5 grid gap-5 sm:grid-cols-2">
              <Field label="Name">
                <input
                  value={room.name}
                  onChange={(e) => patchRoom(room.id, { name: e.target.value })}
                  className="rule w-full border bg-transparent px-2.5 py-1.5 text-[13px]"
                />
              </Field>

              <Field label="Type">
                <select
                  value={room.type}
                  onChange={(e) => patchRoom(room.id, { type: e.target.value as RoomType })}
                  className="rule w-full border bg-transparent px-2.5 py-1.5 text-[13px]"
                >
                  {ROOM_TYPES.map((t) => (
                    <option key={t} value={t} className="bg-[#14171C]">
                      {t}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Waypoint position (x, y, z in metres)">
                <div className="flex gap-1.5">
                  {[0, 1, 2].map((axis) => (
                    <input
                      key={axis}
                      type="number"
                      step="0.05"
                      value={room.waypoint.position[axis]}
                      onChange={(e) => {
                        const position = [...room.waypoint.position] as [
                          number, number, number,
                        ];
                        position[axis] = Number(e.target.value);
                        patchRoom(room.id, {
                          waypoint: { ...room.waypoint, position },
                        });
                      }}
                      className="measure rule w-full border bg-transparent px-2 py-1.5 text-[13px]"
                    />
                  ))}
                </div>
              </Field>

              <Field label="Starting direction (degrees)">
                <input
                  type="number"
                  step="5"
                  value={room.waypoint.yaw}
                  onChange={(e) =>
                    patchRoom(room.id, {
                      waypoint: { ...room.waypoint, yaw: Number(e.target.value) },
                    })
                  }
                  className="measure rule w-full border bg-transparent px-2.5 py-1.5 text-[13px]"
                />
              </Field>

              <Field label="Floor polygon">
                <p className="measure text-[12px] opacity-70">
                  {room.floorPolygon.length} points ·{' '}
                  {Math.abs(polygonArea(room.floorPolygon)).toFixed(2)} m²
                </p>
                <p className="mt-1 text-[12px] opacity-55">
                  Drawing polygons on the splat is not built yet; polygons come from the
                  RoomPlan export or the pipeline hull.
                </p>
              </Field>

              <Field label="Privacy regions">
                <p className="measure text-[12px] opacity-70">
                  {manifest.privacyMasks.length} defined
                </p>
                <p className="mt-1 text-[12px] opacity-55">
                  Blur and delete regions are stored in the manifest; the box-drawing tool
                  is not built yet.
                </p>
              </Field>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[12px] font-medium opacity-65">{label}</span>
      <span className="mt-1.5 block">{children}</span>
    </label>
  );
}
