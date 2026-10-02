/** Public viewer at /p/:projectId */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Assistant, type AssistantAction } from '../components/Assistant';
import { FloorPlan } from '../components/FloorPlan';
import { FloorPanel, FurniturePanel, Sheet } from '../components/Panels';
import { RoomStrip } from '../components/RoomStrip';
import { SplatScene } from '../components/SplatScene';
import { StagedBadge } from '../components/StagedBadge';
import { apiUrl, fileUrl, getJson, postEvent, sessionId } from '../lib/api';
import type { Manifest } from '../lib/manifest';
import { type CatalogItem, stagedRoomIds, useStore } from '../lib/store';
import { isStaged } from '../lib/viewerState';
import { polygonCentroid } from '../lib/geometry';

export function Viewer() {
  const { projectId = '' } = useParams();
  const [searchParams] = useSearchParams();
  const session = useMemo(() => sessionId(), []);

  const {
    manifest, loading, error, viewer, activeRoomId, panel, catalog, showingOriginal,
    setManifest, setError, setCatalog, goToRoom, setPanel, setShowingOriginal,
    setFloor, addItem, recolorItem, reset, loadStateFromUrl, shareUrl,
  } = useStore();

  const [ready, setReady] = useState(false);
  const [fps, setFps] = useState<number | null>(null);
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

  const staged = isStaged(viewer);
  const stagedRooms = useMemo(
    () => stagedRoomIds(viewer, manifest?.items ?? []),
    [viewer, manifest],
  );
  const activeRoom = manifest?.rooms.find((r) => r.id === activeRoomId) ?? null;

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
        if (body.floor_id) setFloor([roomId], body.floor_id);
        for (const placement of body.placements as Array<{
          itemId: string;
          position: [number, number, number];
          yaw: number;
        }>) {
          addItem({
            uid: Math.random().toString(36).slice(2, 9),
            itemId: placement.itemId,
            roomId,
            position: placement.position,
            yaw: placement.yaw,
          });
        }
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
    [projectId, session, setFloor, addItem],
  );

  const applyActions = useCallback(
    (actions: AssistantAction[]) => {
      for (const action of actions) {
        const args = action.args as Record<string, never>;
        if (action.tool === 'set_floor') {
          setFloor(args.roomIds as unknown as string[], args.floorId as unknown as string);
          postEvent(projectId, {
            session_id: session,
            event: 'floor_tried',
            value: args.floorId as unknown as string,
          });
        } else if (action.tool === 'add_item') {
          const roomId = args.roomId as unknown as string;
          addItem({
            uid: Math.random().toString(36).slice(2, 9),
            itemId: args.itemId as unknown as string,
            roomId,
            position: centreOf(roomId),
            yaw: 0,
          });
        } else if (action.tool === 'recolor_item') {
          recolorItem(
            args.itemId as unknown as string,
            args.colorHex as unknown as string,
          );
        } else if (action.tool === 'reset') {
          reset((args.roomId as unknown as string) ?? 'all');
        } else if (action.tool === 'apply_style' || action.tool === 'auto_layout') {
          void runAutoLayout(
            args.roomId as unknown as string,
            args.styleId as unknown as string,
          );
        }
      }
    },
    [setFloor, addItem, recolorItem, reset, runAutoLayout, centreOf, projectId, session],
  );

  async function share() {
    const url = shareUrl();
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    } catch {
      window.prompt('Copy this link', url);
    }
  }

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
    <main className="relative h-full overflow-hidden">
      {manifest && (
        <SplatScene
          manifest={manifest}
          sceneUrl={fileUrl(
            projectId,
            // Prefer the body-without-floor so floor replacement can work.
            manifest.assets?.sceneNoFloor ?? manifest.assets?.scene ?? 'scene.ply',
          )}
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
          onProgress={() => undefined}
          onReady={() => setReady(true)}
          onError={setError}
          onFps={setFps}
        />
      )}

      {(loading || !ready) && !error && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#14171C]">
          {/*
            Spark reports completion but not incremental progress, so this is
            an indeterminate bar rather than a fake percentage.
          */}
          <div className="w-[min(280px,70vw)]">
            <p className="measure text-[12px] opacity-70">Loading the apartment…</p>
            <div className="mt-2 h-px w-full overflow-hidden bg-[rgba(250,249,246,0.2)]">
              <div className="h-px w-1/3 animate-[slide_1.4s_ease-in-out_infinite] bg-[var(--paper)]" />
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
          {activeRoom && (
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

      {/* Floor plan */}
      {manifest && ready && (
        <div
          className="panel absolute left-3 rounded-sm p-2"
          style={{ top: 'calc(5.5rem + var(--safe-top))' }}
        >
          <FloorPlan
            manifest={manifest}
            activeRoomId={activeRoomId}
            stagedRoomIds={stagedRooms}
            onSelectRoom={goToRoom}
          />
        </div>
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

      {fps !== null && ready && (
        <p
          className="measure panel absolute right-3 rounded-sm px-2 py-1 text-[10px] opacity-60"
          style={{ top: 'calc(5.5rem + var(--safe-top))' }}
          data-testid="fps"
        >
          {fps} fps
        </p>
      )}

      {/* Bottom stack */}
      <div
        className="absolute inset-x-0 bottom-0"
        style={{ paddingBottom: 'var(--safe-bottom)' }}
      >
        {panel && manifest && activeRoom && (
          <Sheet
            title={panel === 'floors' ? 'Floors' : panel === 'furniture' ? 'Furniture' : 'Assistant'}
            onClose={() => setPanel(panel)}
          >
            {panel === 'floors' && (
              <FloorPanel
                floors={catalog.floors}
                appliedFloorId={viewer.floors[activeRoom.id]}
                roomName={activeRoom.name}
                onApply={(floorId, allRooms) => {
                  setFloor(allRooms ? ['all'] : [activeRoom.id], floorId);
                  postEvent(projectId, {
                    session_id: session,
                    event: 'floor_tried',
                    value: floorId,
                  });
                }}
                onClear={() => reset(activeRoom.id)}
              />
            )}
            {panel === 'furniture' && (
              <FurniturePanel
                furniture={catalog.furniture}
                styles={catalog.styles}
                onAdd={(itemId) =>
                  addItem({
                    uid: Math.random().toString(36).slice(2, 9),
                    itemId,
                    roomId: activeRoom.id,
                    position: centreOf(activeRoom.id),
                    yaw: 0,
                  })
                }
                onApplyStyle={(styleId) => {
                  const style = catalog.styles.find((s) => s.id === styleId);
                  if (style?.floorIds?.[0]) setFloor([activeRoom.id], style.floorIds[0]);
                  postEvent(projectId, {
                    session_id: session,
                    event: 'style_tried',
                    value: styleId,
                  });
                }}
                onAutoLayout={(styleId) => void runAutoLayout(activeRoom.id, styleId)}
              />
            )}
            {panel === 'assistant' && (
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
            )}
          </Sheet>
        )}

        {manifest && (
          <RoomStrip
            manifest={manifest}
            activeRoomId={activeRoomId}
            stagedRoomIds={stagedRooms}
            onSelect={goToRoom}
          />
        )}

        <nav className="rule flex border-t" aria-label="Tools">
          {(['floors', 'furniture', 'assistant'] as const).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setPanel(id)}
              aria-pressed={panel === id}
              data-testid={`tool-${id}`}
              className={[
                'flex-1 px-3 py-3 text-[12px] font-medium capitalize',
                panel === id ? 'bg-[var(--blueprint)]' : 'panel hover:bg-[rgba(42,47,56,0.95)]',
              ].join(' ')}
            >
              {id}
            </button>
          ))}
          <button
            type="button"
            onClick={share}
            className="panel px-4 py-3 text-[12px] font-medium hover:bg-[rgba(42,47,56,0.95)]"
            data-testid="share"
          >
            {copied ? 'Link copied' : 'Share'}
          </button>
        </nav>
      </div>
    </main>
  );
}
