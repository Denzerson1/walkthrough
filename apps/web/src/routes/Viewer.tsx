/** Public viewer at /p/:projectId */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Assistant, type AssistantAction } from '../components/Assistant';
import { DaylightControl } from '../components/DaylightControl';
import { FloorPlan } from '../components/FloorPlan';
import { DesignMenu } from '../components/DesignMenu';
import { RoomIcon } from '../components/RoomIcon';
import { Sheet } from '../components/Panels';
import { RoomStrip } from '../components/RoomStrip';
import { type Still, SplatScene } from '../components/SplatScene';
import { StagedBadge } from '../components/StagedBadge';
import { TitleScreen } from '../components/TitleScreen';
import { Toast } from '../components/Toast';
import { IS_STATIC, apiUrl, fileUrl, getJson, postEvent, sessionId } from '../lib/api';
import { toMinutes } from '../lib/daylight';
import type { Manifest } from '../lib/manifest';
import { type CatalogItem, stagedRoomIds, useStore } from '../lib/store';
import { isStaged } from '../lib/viewerState';
import { polygonCentroid } from '../lib/geometry';
import { preferredLanguage } from '../lib/listing';
import { doorways, roomArea } from '../lib/rooms';

/** The arguments the assistant's tools carry, as the server defines them. */
interface AssistantArgs {
  roomIds?: string[];
  roomId?: string;
  floorId?: string;
  itemId?: string;
  styleId?: string;
  colorHex?: string;
}

/** Phone-sized screens, in either orientation. */
const COMPACT = '(max-width: 639px), (max-height: 500px)';
/** Wide enough for the design menu as a panel beside the view. */
const WIDE = '(min-width: 1024px) and (min-height: 501px)';

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, [query]);
  return matches;
}

export function Viewer() {
  const { projectId = '' } = useParams();
  const [searchParams] = useSearchParams();
  const session = useMemo(() => sessionId(), []);

  const {
    manifest, error, viewer, activeRoomId, panel, catalog, showingOriginal, selectedUid, toast,
    setManifest, setError, setCatalog, goToRoom, setPanel, setShowingOriginal,
    setFloor, addItems, recolorItem, reset, loadStateFromUrl, shareUrl,
    moveItem, rotateItem, removeItem, selectItem, clearItems, applyStyle, undo, dismissToast,
  } = useStore();

  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState(0);
  // The time of day shown, for a rebuilt flat baked at several; it opens at
  // the time the pipeline made the default.
  const daylightKeys = useMemo(() => {
    const times = manifest?.assets?.lighting?.map((l) => toMinutes(l.time)) ?? [];
    return [...new Set(times)].sort((a, b) => a - b);
  }, [manifest]);
  const opening = useMemo(() => {
    const lighting = manifest?.assets?.lighting;
    if (!lighting?.length) return null;
    const first = lighting.find((l) => l.lightmap === manifest?.assets?.lightmap) ?? lighting[0];
    return toMinutes(first.time);
  }, [manifest]);
  const [chosen, setDaylight] = useState<number | null>(null);
  const daylight = chosen ?? opening;
  const [showHint, setShowHint] = useState(true);
  const touch = useMemo(() => window.matchMedia('(pointer: coarse)').matches, []);
  const compact = useMedia(COMPACT);
  const wide = useMedia(WIDE);
  const [menuOpen, setMenuOpen] = useState(false);
  /** The visitor has pressed "Start tour" (or there is no title screen). */
  const [started, setStarted] = useState(false);
  /** The title screen has faded out after the start. */
  const [titleGone, setTitleGone] = useState(false);
  const language = useMemo(() => preferredLanguage(), []);
  const stillRef = useRef<(() => Still) | null>(null);
  const [editing, setEditing] = useState(false);
  const [entryNote, setEntryNote] = useState<string | null>(null);
  const reducedMotion = useMedia('(prefers-reduced-motion: reduce)');
  const [pose, setPose] = useState<{ x: number; z: number; yaw: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const [layoutNote, setLayoutNote] = useState<string | null>(null);

  // Load the manifest, catalog and any shared state.
  useEffect(() => {
    let cancelled = false;
    const shared = searchParams.get('s');
    if (shared) loadStateFromUrl(shared);

    getJson<Manifest>(`/api/projects/${projectId}/manifest`)
      .then((m) => {
        if (!cancelled) setManifest(m);
      })
      .catch(() => {
        if (!cancelled) {
          setError(
            `No apartment called "${projectId}". Generate the test scene with ` +
              '`pnpm seed:testscene`, or check the link.',
          );
        }
      });

    for (const kind of ['floors', 'furniture', 'styles'] as const) {
      getJson<CatalogItem[]>(`/api/catalog/${kind}`)
        .then((items) => !cancelled && setCatalog({ [kind]: items }))
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [projectId, searchParams, setManifest, setError, setCatalog, loadStateFromUrl]);

  // An editor signed in (on the server, not the static site) can set the entry view.
  useEffect(() => {
    if (IS_STATIC) return;
    fetch(apiUrl('/api/editor/session'), { credentials: 'include' })
      .then((r) => setEditing(r.ok))
      .catch(() => setEditing(false));
  }, []);

  async function saveEntryView() {
    const still = stillRef.current?.();
    if (!still) return;
    setEntryNote('Saving the entry view…');
    const response = await fetch(apiUrl(`/api/projects/${projectId}/entry`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(still),
    }).catch(() => null);
    setEntryNote(response?.ok ? 'Entry view saved' : 'Could not save the entry view');
    setTimeout(() => setEntryNote(null), 2500);
  }

  // Room dwell analytics.
  useEffect(() => {
    if (!activeRoomId || !manifest) return;
    postEvent(projectId, { session_id: session, event: 'room_viewed', room_id: activeRoomId });
    const enteredAt = Date.now();
    return () => {
      postEvent(projectId, {
        session_id: session,
        event: 'room_dwell',
        room_id: activeRoomId,
        duration_ms: Date.now() - enteredAt,
      });
    };
  }, [activeRoomId, manifest, projectId, session]);

  // The hint earns its place only until the person has actually moved.
  useEffect(() => {
    if (!showHint) return;
    const dismiss = () => setShowHint(false);
    window.addEventListener('keydown', dismiss, { once: true });
    window.addEventListener('wheel', dismiss, { once: true, passive: true });
    window.addEventListener('pointerdown', dismiss, { once: true });
    return () => {
      window.removeEventListener('keydown', dismiss);
      window.removeEventListener('wheel', dismiss);
      window.removeEventListener('pointerdown', dismiss);
    };
  }, [showHint]);

  // Esc closes what is open: the menu, the assistant, a selection.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setMenuOpen(false);
      if (panel) setPanel(panel);
      selectItem(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [panel, setPanel, selectItem]);

  const onSelectPiece = useCallback(
    (uid: string | null) => {
      selectItem(uid);
      if (useStore.getState().viewer.items.find((i) => i.uid === uid)?.slotId) setMenuOpen(true);
    },
    [selectItem],
  );

  // The arrival note shows itself once each time it mounts, for a new room.
  const arrive = useCallback((node: HTMLElement | null) => {
    node?.animate(
      [
        { opacity: 0, transform: 'translate(-50%, 6px)' },
        { opacity: 1, transform: 'translate(-50%, 0)', offset: 0.12 },
        { opacity: 1, transform: 'translate(-50%, 0)', offset: 0.8 },
        { opacity: 0, transform: 'translate(-50%, 0)' },
      ],
      { duration: reducedMotion ? 1600 : 2200, fill: 'forwards' },
    );
  }, [reducedMotion]);

  // A label at the way into each neighbouring room; the one you are in has none.
  const labels = useMemo(() => {
    if (!manifest || !activeRoomId) return [];
    return doorways(manifest.rooms, activeRoomId).flatMap(({ roomId, along }) => {
      const room = manifest.rooms.find((r) => r.id === roomId);
      return room
        ? [{ id: room.id, text: room.name, icon: <RoomIcon type={room.type} />, along }]
        : [];
    });
  }, [manifest, activeRoomId]);

  const staged = isStaged(viewer);
  const stagedRooms = useMemo(
    () => stagedRoomIds(viewer, manifest?.items ?? []),
    [viewer, manifest],
  );
  const activeRoom = manifest?.rooms.find((r) => r.id === activeRoomId) ?? null;
  const selectedItem = viewer.items.find((i) => i.uid === selectedUid) ?? null;
  const selectedItemName =
    catalog.furniture.find((f) => f.id === selectedItem?.itemId)?.name ?? 'Item';

  const centreOf = useCallback(
    (roomId: string): [number, number, number] => {
      const room = manifest?.rooms.find((r) => r.id === roomId);
      if (!room || room.floorPolygon.length < 3) return [0, 0, 0];
      // Centroid rather than the bounding-box centre: for an L-shaped room
      // the box centre can fall outside the polygon entirely.
      const [cx, cz] = polygonCentroid(room.floorPolygon);
      return [cx, 0, cz];
    },
    [manifest],
  );

  /**
   * Ask the server to lay the style out properly. The deterministic solver
   * lives server-side (walkthrough_pipeline.layout), so the viewer and the
   * assistant both get real placements rather than a pile at the centre.
   */
  const runAutoLayout = useCallback(
    async (roomId: string, styleId: string) => {
      try {
        const response = await fetch(apiUrl('/api/layout'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: projectId, room_id: roomId, style_id: styleId }),
        });
        if (!response.ok) {
          const detail = await response
            .json()
            .then((b) => b.detail as string)
            .catch(() => 'Could not lay this room out.');
          setLayoutNote(detail);
          return;
        }
        const body = await response.json();
        // Furnishing a room is a replacement, not an addition: switching
        // theme twice would otherwise leave two sets of furniture standing
        // inside each other.
        clearItems(roomId);
        if (body.floor_id) setFloor([roomId], body.floor_id);
        const placements = body.placements as Array<{
          itemId: string;
          position: [number, number, number];
          yaw: number;
        }>;
        addItems(
          placements.map((p) => ({
            itemId: p.itemId,
            roomId,
            position: p.position,
            yaw: p.yaw,
          })),
        );
        setLayoutNote(
          body.skipped?.length
            ? `Placed ${body.placements.length}; ${body.skipped.length} did not fit.`
            : null,
        );
        postEvent(projectId, { session_id: session, event: 'style_tried', value: styleId });
      } catch {
        setLayoutNote('Could not reach the layout service.');
      }
    },
    [projectId, session, setFloor, addItems, clearItems],
  );

  const applyActions = useCallback(
    (actions: AssistantAction[]) => {
      for (const action of actions) {
        // The server validates every id in these args against the catalog
        // before they reach us, so the shape is all that needs naming.
        const args = action.args as AssistantArgs;
        if (action.tool === 'set_floor' && args.floorId) {
          setFloor(args.roomIds ?? [], args.floorId);
          postEvent(projectId, {
            session_id: session,
            event: 'floor_tried',
            value: args.floorId,
          });
        } else if (action.tool === 'add_item' && args.roomId && args.itemId) {
          addItems([
            {
              itemId: args.itemId,
              roomId: args.roomId,
              position: centreOf(args.roomId),
              yaw: 0,
            },
          ]);
        } else if (action.tool === 'recolor_item' && args.itemId && args.colorHex) {
          recolorItem(args.itemId, args.colorHex);
        } else if (action.tool === 'reset') {
          reset(args.roomId ?? 'all');
        } else if (action.tool === 'apply_style' && args.styleId) {
          // The same store action as the menu, so both place the same pieces.
          applyStyle(args.roomId ? [args.roomId] : 'all', args.styleId);
        } else if (action.tool === 'auto_layout') {
          if (args.roomId && args.styleId) void runAutoLayout(args.roomId, args.styleId);
        }
      }
    },
    [setFloor, addItems, recolorItem, reset, applyStyle, runAutoLayout, centreOf, projectId, session],
  );

  async function share() {
    const url = shareUrl();
    // On a phone, the phone's own share sheet: straight into a message.
    if (touch && navigator.share) {
      try {
        await navigator.share({ title: manifest?.name, url });
        return;
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    } catch {
      window.prompt('Copy this link', url);
    }
  }

  // Top right on a big screen; on a phone, at the bottom where a thumb reaches it.
  const daylightControl = manifest && ready && daylightKeys.length > 1 && daylight !== null && (
    <DaylightControl keys={daylightKeys} value={daylight} onChange={setDaylight} />
  );

  if (error) {
    return (
      <main className="flex h-full items-center justify-center p-8">
        <div className="max-w-[52ch]">
          <h1 className="text-[20px] font-semibold tracking-tight">This apartment is not here</h1>
          <p className="mt-2 text-[14px] leading-relaxed opacity-75">{error}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="viewer relative h-full overflow-hidden">
      {manifest && (
        <SplatScene
          manifest={manifest}
          sceneUrl={fileUrl(
            projectId,
            // Prefer the body-without-floor so floor replacement can work.
            manifest.assets?.sceneNoFloor ?? manifest.assets?.scene ?? 'scene.ply',
          )}
          model={
            manifest.assets?.model && manifest.assets.lightmap
              ? {
                  url: fileUrl(projectId, manifest.assets.model),
                  lightmapUrl: fileUrl(projectId, manifest.assets.lightmap),
                  lightmapScale: manifest.assets.lightmapScale ?? 1,
                  lighting: manifest.assets.lighting?.map((l) => ({
                    minutes: toMinutes(l.time),
                    lightmapUrl: fileUrl(projectId, l.lightmap),
                    lightmapScale: l.lightmapScale,
                    skyTint: l.skyTint,
                    skyUrl: l.sky ? fileUrl(projectId, l.sky) : null,
                    // three lowercases a glTF custom attribute's name.
                    colours: l.colours?.toLowerCase() ?? null,
                  })),
                  sunPath: manifest.assets.sunPath,
                  viewPhoto: manifest.assets.viewPhoto
                    ? {
                        url: fileUrl(projectId, manifest.assets.viewPhoto.model),
                        minutes: toMinutes(manifest.assets.viewPhoto.time),
                      }
                    : undefined,
                }
              : undefined
          }
          daylight={daylight ?? undefined}
          floorUrl={
            manifest.assets?.sceneNoFloor && manifest.assets?.floor
              ? fileUrl(projectId, manifest.assets.floor)
              : undefined
          }
          activeRoomId={activeRoomId}
          viewer={viewer}
          floorCatalog={catalog.floors}
          furnitureCatalog={catalog.furniture}
          showingOriginal={showingOriginal}
          onProgress={setProgress}
          onReady={() => setReady(true)}
          onError={setError}
          onRoomChange={goToRoom}
          onPose={(x, z, yaw) => setPose({ x, z, yaw })}
          selectedUid={selectedUid}
          onSelectItem={onSelectPiece}
          labels={ready && (started || !manifest.heroView) ? labels : []}
          onLabel={goToRoom}
          entry={manifest.heroView ?? null}
          entering={started}
          stillRef={stillRef}
          onMoveItem={moveItem}
        />
      )}

      {/* A lens's falloff at the corners: without it the rebuilt flat reads as a render. */}
      {manifest?.assets?.model && (
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background: 'radial-gradient(ellipse at center, transparent 58%, rgba(20,16,12,0.22) 100%)',
          }}
        />
      )}

      {manifest?.heroView && !titleGone && (
        <TitleScreen
          name={manifest.name}
          listing={manifest.listing}
          stillUrl={manifest.heroView.image ? fileUrl(projectId, manifest.heroView.image) : null}
          language={language}
          ready={ready}
          progress={progress}
          leaving={started}
          onStart={() => setStarted(true)}
          onGone={() => setTitleGone(true)}
        />
      )}

      {editing && ready && (
        <div className="absolute left-1/2 z-10 -translate-x-1/2" style={{ top: 'calc(0.75rem + var(--safe-top))' }}>
          <button
            type="button"
            onClick={saveEntryView}
            className="btn tap min-h-11 px-4 text-[13px]"
            data-testid="set-entry-view"
          >
            {entryNote ?? 'Set current view as entry view'}
          </button>
        </div>
      )}

      {!ready && !error && !manifest?.heroView && (
        <div className="absolute inset-0 flex items-center justify-center bg-[var(--paper-warm)]">
          {/*
            A rebuilt flat reports how much of its model has arrived. Spark
            reports completion but not incremental progress, so a splat scene
            gets an indeterminate bar rather than a fake percentage.
          */}
          <div className="w-[min(280px,70vw)]">
            <p className="measure flex justify-between text-[12px] opacity-70">
              <span>Loading the apartment…</span>
              {progress > 0 && <span>{Math.round(progress * 100)}%</span>}
            </p>
            <div className="mt-2 h-px w-full overflow-hidden bg-[rgba(42,38,34,0.18)]">
              {progress > 0 ? (
                <div
                  className="h-px bg-[var(--accent)] transition-[width] duration-200"
                  style={{ width: `${progress * 100}%` }}
                />
              ) : (
                <div className="h-px w-1/3 animate-[slide_1.4s_ease-in-out_infinite] bg-[var(--accent)]" />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Top bar */}
      <header
        className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-3"
        style={{ paddingTop: 'calc(0.75rem + var(--safe-top))' }}
      >
        <div className="pointer-events-auto panel px-3 py-2">
          <h1 className="text-[13px] font-semibold leading-tight tracking-tight">
            {manifest?.name ?? 'walkthrough'}
          </h1>
          {/* A flat rebuilt as one room has no room name worth showing. */}
          {activeRoom && manifest && manifest.rooms.length > 1 && (
            <p className="measure text-[11px] opacity-65">{activeRoom.name}</p>
          )}
        </div>
        <div className="pointer-events-auto">
          <StagedBadge
            staged={staged}
            showingOriginal={showingOriginal}
            onHoldStart={() => setShowingOriginal(true)}
            onHoldEnd={() => setShowingOriginal(false)}
            onReset={() => reset('all')}
          />
        </div>
      </header>

      {wide && activeRoom && ready && (
        <div
          className="absolute right-3 flex flex-col items-end"
          style={{
            top: 'calc(10.25rem + var(--safe-top))',
            bottom: 'calc(4.5rem + var(--safe-bottom))',
          }}
        >
          <DesignMenu room={activeRoom} compact={false} open={menuOpen} onOpenChange={setMenuOpen} />
        </div>
      )}

      {!compact && daylightControl && (
        <div
          className="panel absolute right-3 w-[min(260px,46vw)] px-3 py-2"
          style={{ top: 'calc(5.5rem + var(--safe-top))' }}
        >
          {daylightControl}
        </div>
      )}

      {/* Floor plan: smaller on a phone, and out of the way of an open sheet. */}
      {manifest && ready && !(compact && panel) && (
        <div
          className="panel absolute left-3 rounded-sm p-2"
          style={{
            top: compact ? 'calc(4.25rem + var(--safe-top))' : 'calc(5.5rem + var(--safe-top))',
          }}
        >
          <FloorPlan
            manifest={manifest}
            activeRoomId={activeRoomId}
            stagedRoomIds={stagedRooms}
            onSelectRoom={goToRoom}
            pose={pose}
            size={compact ? 84 : undefined}
          />
        </div>
      )}

      {/* Where you have arrived: once per room, briefly. */}
      {ready && activeRoom && manifest && manifest.rooms.length > 1 && (
        <p
          key={activeRoom.id}
          ref={arrive}
          className="panel pointer-events-none absolute left-1/2 flex items-center gap-1.5 rounded-full px-4 py-2 text-[13px] font-medium opacity-0"
          style={{ top: 'calc(5.5rem + var(--safe-top))' }}
          role="status"
          data-testid="arrival"
        >
          <RoomIcon type={activeRoom.type} />
          {activeRoom.name}
          <span className="measure font-normal opacity-65">· {Math.round(roomArea(activeRoom))} m²</span>
        </p>
      )}

      {layoutNote && (
        <p
          className="panel absolute left-1/2 w-[min(32ch,80vw)] -translate-x-1/2 border-l-2 px-3 py-2 text-[12px]"
          style={{ top: 'calc(5.5rem + var(--safe-top))', borderColor: 'var(--staged)' }}
          role="status"
          data-testid="layout-note"
          onClick={() => setLayoutNote(null)}
        >
          {layoutNote}
        </p>
      )}

      {/*
        How to walk. Shown once per session on arrival and dismissed by the
        first move, because a free-roam tour is not discoverable without it —
        people expect a property viewer to be click-to-jump.
      */}
      {ready && showHint && (
        <div
          className="panel pointer-events-none absolute left-1/2 -translate-x-1/2 rounded-[var(--radius)] px-4 py-2"
          style={{ bottom: 'calc(9.5rem + var(--safe-bottom))', animation: 'fade-up 300ms ease-out' }}
          data-testid="walk-hint"
        >
          <p className="text-[12.5px]">
            {touch ? (
              <>
                <span className="font-medium">Tap the floor to walk</span>
                <span className="opacity-60"> · drag to look · pinch to zoom</span>
              </>
            ) : (
              <>
                <span className="font-medium">Click the floor to walk there</span>
                <span className="opacity-60"> · drag to look around</span>
              </>
            )}
          </p>
        </div>
      )}

      {/* Bottom stack */}
      <div
        className="absolute inset-x-0 bottom-0"
        style={{ paddingBottom: 'var(--safe-bottom)' }}
      >
        {panel === 'assistant' && manifest && activeRoom && (
          <Sheet title="Assistant" onClose={() => setPanel(panel)}>
            <div className="h-[46vh]">
              <Assistant
                projectId={projectId}
                sessionId={session}
                context={{
                  activeRoomId: activeRoom.id,
                  rooms: manifest.rooms.map((r) => ({
                    id: r.id,
                    name: r.name,
                    type: r.type,
                  })),
                  appliedFloors: viewer.floors,
                  placedItems: viewer.items,
                  recolors: viewer.recolors,
                  manifestItemIds: manifest.items.map((i) => i.id),
                }}
                onActions={applyActions}
              />
            </div>
          </Sheet>
        )}

        {selectedItem && !selectedItem.slotId && (
          <div className="flex justify-center px-3 pb-2" data-testid="item-controls">
            <div className="panel flex items-center gap-1 rounded-[9px] p-1">
              <span className="px-3 text-[12px] font-medium">{selectedItemName}</span>
              <button
                type="button"
                onClick={() => rotateItem(selectedItem.uid, -15)}
                className="tap rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[13px] hover:bg-[var(--paper-warm)]"
                aria-label="Rotate left"
              >
                ⟲
              </button>
              <button
                type="button"
                onClick={() => rotateItem(selectedItem.uid, 15)}
                className="tap rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[13px] hover:bg-[var(--paper-warm)]"
                aria-label="Rotate right"
              >
                ⟳
              </button>
              <button
                type="button"
                onClick={() => removeItem(selectedItem.uid)}
                className="tap rounded-[var(--radius-sm)] px-3 py-1.5 text-[12px] font-medium text-[var(--staged)] hover:bg-[var(--paper-warm)]"
                data-testid="remove-item"
              >
                Remove
              </button>
            </div>
          </div>
        )}

        <Toast toast={toast} onUndo={undo} onDismiss={dismissToast} />

        {manifest && manifest.rooms.length > 1 && !(menuOpen && !wide) && (
          <RoomStrip
            manifest={manifest}
            activeRoomId={activeRoomId}
            stagedRoomIds={stagedRooms}
            onSelect={goToRoom}
          />
        )}

        {!wide && activeRoom && ready && (
          <div className="px-3 pb-1.5">
            <DesignMenu room={activeRoom} compact open={menuOpen} onOpenChange={setMenuOpen} />
          </div>
        )}

        {compact && !panel && !menuOpen && daylightControl && (
          <div className="flex justify-center px-3 pb-1.5">
            <div className="panel w-full max-w-[360px] rounded-[9px] px-3 py-1.5">
              {daylightControl}
            </div>
          </div>
        )}

        <nav
          className="flex items-center justify-center gap-1.5 px-3 pb-3 pt-1"
          aria-label="Tools"
        >
          <div className="panel flex items-center gap-0.5 rounded-[9px] p-1">
            {/* The assistant runs on the server, which a static site does not have. */}
            {!IS_STATIC && (
              <button
                type="button"
                onClick={() => setPanel('assistant')}
                aria-pressed={panel === 'assistant'}
                data-testid="tool-assistant"
                className={[
                  'tap rounded-[var(--radius-sm)] px-4 py-1.5 text-[13px] font-medium',
                  'transition-colors',
                  panel === 'assistant'
                    ? 'bg-[var(--accent)] text-[var(--paper)]'
                    : 'text-[var(--ink)] hover:bg-[var(--paper-warm)]',
                ].join(' ')}
              >
                Assistant
              </button>
            )}
            <span className="mx-0.5 h-4 w-px bg-[var(--line)]" aria-hidden="true" />
            <button
              type="button"
              onClick={share}
              className="tap rounded-[var(--radius-sm)] px-4 py-1.5 text-[13px] font-medium text-[var(--ink)] transition-colors hover:bg-[var(--paper-warm)]"
              data-testid="share"
            >
              {copied ? 'Copied' : 'Share'}
            </button>
          </div>
        </nav>
      </div>
    </main>
  );
}
