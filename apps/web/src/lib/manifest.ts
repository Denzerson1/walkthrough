/**
 * Types mirroring scene/manifest.json as specified in docs/BRIEF.md §4.
 * Keep in sync with services/api/src/walkthrough_api/models.py and docs/SPEC.md §4.
 */

export type Vec2 = [number, number];
export type Vec3 = [number, number, number];
export type Mat4 = [
  [number, number, number, number],
  [number, number, number, number],
  [number, number, number, number],
  [number, number, number, number],
];

export type RoomType =
  | 'living'
  | 'bedroom'
  | 'kitchen'
  | 'dining'
  | 'bathroom'
  | 'hallway'
  | 'office'
  | 'other';

export interface Waypoint {
  /** Camera position in metres. y is eye height above the floor plane. */
  position: Vec3;
  /** Yaw in degrees, 0 = looking down -Z, increasing counter-clockwise. */
  yaw: number;
}

export interface Wall {
  start: Vec2;
  end: Vec2;
  height: number;
}

export interface Opening {
  type: 'door' | 'window';
  /** Index into the room's walls array. */
  wallIndex: number;
  /**
   * Distance in metres from the wall's start point to the opening's CENTRE.
   * The opening spans offset +/- width/2. Both the layout solver
   * (layout.py Wall.free_spans) and the floor plan read it this way.
   */
  offset: number;
  width: number;
  height: number;
}

export interface Room {
  id: string;
  name: string;
  type: RoomType;
  waypoint: Waypoint;
  /** Floor outline in metres on the y=0 plane, counter-clockwise. */
  floorPolygon: Vec2[];
  walls: Wall[];
  openings: Opening[];
  /**
   * Average colour of this room's original floor splats, as #rrggbb. Used to
   * draw an unreplaced room once the floor splats are hidden because another
   * room has a replacement floor.
   */
  originalFloorColor?: string;
}

export interface ItemBox {
  center: Vec3;
  size: Vec3;
  /** Rotation about the Y axis in degrees. */
  yaw: number;
}

export interface ManifestItem {
  id: string;
  label: string;
  roomId: string;
  box: ItemBox;
  source: 'roomplan' | 'editor';
}

export interface PrivacyMask {
  id: string;
  /** 'blur' softens the region, 'delete' removes the splats entirely. */
  mode: 'blur' | 'delete';
  box: ItemBox;
}

export interface Manifest {
  id: string;
  name: string;
  units: 'm';
  upAxis: 'y';
  transform: Mat4;
  floor: { plane: [number, number, number, number] };
  rooms: Room[];
  items: ManifestItem[];
  privacyMasks: PrivacyMask[];
  /** Relative paths under the project's scene/ directory. Added by the pipeline. */
  assets?: {
    scene?: string;
    sceneNoFloor?: string;
    floor?: string;
    floorShading?: Record<string, string>;
  };
}

export const IDENTITY_TRANSFORM: Mat4 = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

export function findRoom(manifest: Manifest, roomId: string): Room | undefined {
  return manifest.rooms.find((r) => r.id === roomId);
}
