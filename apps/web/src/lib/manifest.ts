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
  /**
   * Height of the lower edge above the floor. Absent when unknown: a door
   * then starts at the floor and a window at 0.9 m (manifest.py DEFAULT_SILL_M).
   */
  sill?: number | null;
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
  /**
   * The ceiling as scanned, piece by piece: 3D outlines, flat or sloped. Where
   * a slope comes down, a person cannot stand upright; see `canStand`.
   */
  ceilings?: Vec3[][];
  /** The rebuilt flat's pieces standing on the floor, as outlines; see `canStand`. */
  obstacles?: Obstacle[];
}

export interface Obstacle {
  id: string;
  footprint: Vec2[];
}

/** The sun at one time of day, as the viewer draws its direct light. */
export interface SunSample {
  /** After midnight. */
  minutes: number;
  /** Towards the sun, in scene space. */
  direction: [number, number, number];
  /** Its direct light on a surface facing it, in the light maps' units. */
  light: [number, number, number];
}

/** A rebuilt flat's light at one time of day. */
export interface Lighting {
  /** "17:30" */
  time: string;
  lightmap: string;
  lightmapScale: number;
  exposure?: number;
  /** Multiplies the sky dome's colours: warm and dim towards dusk. */
  skyTint?: number[] | null;
  /** The vertex attribute (glTF `_LIGHT_<time>`) holding the light in vertices at this time. */
  colours?: string | null;
  /** The sky at this time: an equirectangular panorama, u = 0.5 along -z, zenith at the top. */
  sky?: string | null;
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
  /** The piece's own model (scene/models/*.glb), fitted into `box`. */
  model?: string | null;
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
    /** The rebuilt flat with baked lighting; shown instead of the splat. */
    model?: string | null;
    /** Its baked light: an 8-bit atlas on the model's second UV set, holding light / lightmapScale. */
    lightmap?: string | null;
    lightmapScale?: number;
    /** The same at other times of day, in the order they were baked. */
    lighting?: Lighting[];
    /**
     * The sun along the slider's span, every few minutes: the light maps hold
     * all but its direct light, which the viewer draws, so it moves. Absent
     * or empty: the sun is in the light maps.
     */
    sunPath?: SunSample[];
    /** The view out of the windows from the owner's photos: a .glb, and when it was taken. */
    viewPhoto?: { model: string; time: string } | null;
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
