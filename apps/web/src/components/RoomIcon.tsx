/** A small line drawing of what a room is for, beside its name. */

import type { RoomType } from '../lib/manifest';

const PATHS: Partial<Record<RoomType, string>> = {
  // A sofa.
  living: 'M3 11V8a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v3M2 11h12v3H2zM3 14v1.5M13 14v1.5',
  // A bed with its pillow.
  bedroom: 'M2 13V5M2 10h12v3M14 13v-3a2 2 0 0 0-2-2H7v2M4 8.5h2',
  // A door, ajar.
  hallway: 'M4 14V2h8v12M2 14h12M9.5 8.5h.01',
  // A pot on a hob.
  kitchen: 'M3 7h10v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM1.5 8.5H3M13 8.5h1.5M6 4.5V3M10 4.5V3',
  // A table and a chair.
  dining: 'M2 6h12M4 6v8M12 6v8M6.5 10h3',
  // A bath.
  bathroom: 'M2 8h12v2a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4zM4 8V3.5A1.5 1.5 0 0 1 6.5 3',
  // A desk and a screen.
  office: 'M2 13h12M4 13V9h8v4M6 9V4h4v5',
};

export function RoomIcon({ type }: { type: RoomType }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0 opacity-75"
    >
      <path d={PATHS[type] ?? 'M3 3h10v10H3z'} />
    </svg>
  );
}
