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
      className="scroll-x flex items-stretch gap-px"
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
              'group relative shrink-0 px-4 py-3 text-left transition-colors',
              isActive ? 'bg-[rgba(47,95,208,0.9)]' : 'bg-[rgba(20,23,28,0.78)] hover:bg-[rgba(42,47,56,0.9)]',
            ].join(' ')}
            style={{ minWidth: 112 }}
          >
            <span className="block text-[13px] font-medium leading-tight">{room.name}</span>
            <span className="measure mt-0.5 block text-[11px] opacity-70">
              {area > 0.01 ? `${area.toFixed(1)} m²` : '—'}
            </span>
            {stagedRoomIds.has(room.id) && (
              <span
                aria-label="Virtually staged"
                className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full"
                style={{ background: 'var(--staged)' }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}
