/**
 * Room navigation. Rooms are listed with their real floor area, because that
 * is what someone deciding between apartments actually wants to compare.
 */

import { useEffect, useRef } from 'react';
import type { Manifest } from '../lib/manifest';
import { polygonArea } from '../lib/geometry';

interface Props {
  manifest: Manifest;
  activeRoomId: string | null;
  stagedRoomIds: Set<string>;
  onSelect: (roomId: string) => void;
}

export function RoomStrip({ manifest, activeRoomId, stagedRoomIds, onSelect }: Props) {
  const listRef = useRef<HTMLDivElement>(null);

  // Keep the current room in view when the assistant or a share link moves us.
  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    node?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }, [activeRoomId]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const index = manifest.rooms.findIndex((r) => r.id === activeRoomId);
    if (e.key === 'ArrowRight' && index < manifest.rooms.length - 1) {
      onSelect(manifest.rooms[index + 1].id);
    } else if (e.key === 'ArrowLeft' && index > 0) {
      onSelect(manifest.rooms[index - 1].id);
    } else {
      return;
    }
    e.preventDefault();
    // The scene listens for arrow keys on window to turn the camera. Without
    // this, one press would both change room and rotate the view.
    e.stopPropagation();
    e.nativeEvent.stopImmediatePropagation();
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label="Rooms"
      onKeyDown={onKeyDown}
      className="scroll-x flex items-stretch gap-1.5 px-3 pb-1"
      data-testid="room-strip"
    >
      {manifest.rooms.map((room) => {
        const isActive = room.id === activeRoomId;
        const area = Math.abs(polygonArea(room.floorPolygon));
        return (
          <button
            key={room.id}
            role="tab"
            aria-selected={isActive}
            data-active={isActive}
            data-room-id={room.id}
            onClick={() => onSelect(room.id)}
            className={[
              'group relative shrink-0 rounded-[var(--radius)] px-3.5 py-1.5 text-left transition-colors',
              isActive
                ? 'bg-[var(--accent)] text-[var(--paper)]'
                : 'panel text-[var(--ink)] hover:bg-[var(--paper)]',
            ].join(' ')}
          >
            <span className="flex items-baseline gap-2 whitespace-nowrap">
              <span className="text-[13px] font-medium leading-tight">{room.name}</span>
              <span className={`measure text-[11px] ${isActive ? 'opacity-70' : 'opacity-55'}`}>
                {area > 0.01 ? `${area.toFixed(1)} m²` : '—'}
              </span>
            </span>
            {stagedRoomIds.has(room.id) && (
              <span
                aria-label="Changed from the original"
                className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full"
                style={{ background: 'var(--staged)' }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}
