/**
 * Mini floor plan.
 *
 * Drawn the way an architect draws a plan — poché walls, door swings, a
 * position marker with a view cone — rather than as abstract dots. It is the
 * one place in the interface allowed to be visually loud, because it is the
 * only element that tells you where you are standing in a real building.
 */

import { useMemo } from 'react';
import { polygonBounds } from '../lib/geometry';
import { findRoom } from '../lib/manifest';
import type { Manifest, Room, Vec2 } from '../lib/manifest';

interface Props {
  manifest: Manifest;
  activeRoomId: string | null;
  stagedRoomIds: Set<string>;
  onSelectRoom: (roomId: string) => void;
  /** Compact form for the viewer overlay; expanded for the editor. */
  size?: number;
}

const PADDING = 10;

export function FloorPlan({
  manifest,
  activeRoomId,
  stagedRoomIds,
  onSelectRoom,
  size = 132,
}: Props) {
  const { bounds, scale } = useMemo(() => computeBounds(manifest, size), [manifest, size]);
  const active = activeRoomId ? findRoom(manifest, activeRoomId) : undefined;

  const toX = (x: number) => (x - bounds.minX) * scale + PADDING;
  const toY = (z: number) => (z - bounds.minZ) * scale + PADDING;

  const width = (bounds.maxX - bounds.minX) * scale + PADDING * 2;
  const height = (bounds.maxZ - bounds.minZ) * scale + PADDING * 2;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Floor plan"
      data-testid="floor-plan"
      className="overflow-visible"
    >
      {manifest.rooms.map((room) => {
        const isActive = room.id === activeRoomId;
        const isStaged = stagedRoomIds.has(room.id);
        const points = room.floorPolygon.map((p) => `${toX(p[0])},${toY(p[1])}`).join(' ');
        return (
          <g key={room.id}>
            <polygon
              points={points}
              fill={
                isActive
                  ? 'rgba(47, 95, 208, 0.28)'
                  : isStaged
                    ? 'rgba(232, 163, 61, 0.14)'
                    : 'rgba(250, 249, 246, 0.07)'
              }
              stroke={isActive ? '#8fa8e4' : 'rgba(250, 249, 246, 0.5)'}
              strokeWidth={isActive ? 1.6 : 1}
              style={{ cursor: 'pointer' }}
              onClick={() => onSelectRoom(room.id)}
            >
              <title>{room.name}</title>
            </polygon>
            {room.openings.map((opening, i) => {
              const wall = room.walls[opening.wallIndex];
              if (!wall) return null;
              const span = openingSpan(wall.start, wall.end, opening.offset, opening.width);
              return (
                <line
                  key={i}
                  x1={toX(span[0][0])}
                  y1={toY(span[0][1])}
                  x2={toX(span[1][0])}
                  y2={toY(span[1][1])}
                  stroke={opening.type === 'door' ? '#14171C' : '#8fa8e4'}
                  strokeWidth={opening.type === 'door' ? 3 : 2}
                  strokeLinecap="butt"
                />
              );
            })}
          </g>
        );
      })}

      {active && (
        <ViewMarker
          x={toX(active.waypoint.position[0])}
          y={toY(active.waypoint.position[2])}
          yaw={active.waypoint.yaw}
        />
      )}
    </svg>
  );
}

/** Position dot with the direction you are facing. */
function ViewMarker({ x, y, yaw }: { x: number; y: number; yaw: number }) {
  // Manifest yaw 0 looks down -Z, which is up on the plan.
  const rotation = -yaw;
  return (
    <g transform={`translate(${x} ${y}) rotate(${rotation})`}>
      <path d="M 0 0 L -9 -16 A 18 18 0 0 1 9 -16 Z" fill="rgba(143, 168, 228, 0.45)" />
      <circle r="3.4" fill="#FAF9F6" stroke="#2F5FD0" strokeWidth="1.6" />
    </g>
  );
}

function openingSpan(
  start: Vec2,
  end: Vec2,
  offset: number,
  width: number,
): [Vec2, Vec2] {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  const a = offset - width / 2;
  const b = offset + width / 2;
  return [
    [start[0] + ux * a, start[1] + uy * a],
    [start[0] + ux * b, start[1] + uy * b],
  ];
}

function computeBounds(manifest: Manifest, size: number) {
  const points: Vec2[] = manifest.rooms.flatMap((r: Room) => r.floorPolygon);
  if (!points.length) {
    return { bounds: { minX: 0, minZ: 0, maxX: 1, maxZ: 1 }, scale: size };
  }
  const { min, max } = polygonBounds(points);
  const bounds = { minX: min[0], maxX: max[0], minZ: min[1], maxZ: max[1] };
  const spanX = Math.max(bounds.maxX - bounds.minX, 0.001);
  const spanZ = Math.max(bounds.maxZ - bounds.minZ, 0.001);
  const scale = Math.min(size / spanX, size / spanZ);
  return { bounds, scale };
}
