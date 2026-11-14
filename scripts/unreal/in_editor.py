"""
Runs inside the Unreal editor (unreal/flat02), started by scripts/unreal/run.py.

FLAT02_MODE=build   the level, from an empty one: Cesium's georeference at the flat,
                    Google's tiles streamed (token from the environment), the sun for
                    the date and time, the flat imported; the ground under it measured
                    in the tiles (in a play session), the flat placed by the measured
                    bearings at its floor's height; glass, sheers, lamps, cameras.
FLAT02_MODE=render  Movie Render Queue (render.py): the 4K still and the 30 s walk.
FLAT02_MODE=dev     keeps the editor open and runs snippets (dev.sh).

Everything it measures goes to data/projects/flat-02/unreal/report.json.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import unreal

DATA = Path(os.environ["FLAT02_DATA"])
SCENE = json.loads((DATA / "scene.json").read_text(encoding="utf-8"))
MODE = os.environ.get("FLAT02_MODE", "build")
LEVEL = "/Game/Flat02/Flat02"
MODEL_DIR = "/Game/Flat02/Model"
GOOGLE_TILES = 2275207
APERTURE = 5.6
REPORT = DATA / "report.json"
report: dict = (json.loads(REPORT.read_text(encoding="utf-8"))
                if REPORT.exists() and MODE != "build" else {})

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def log(*parts) -> None:
    unreal.log("[flat02] " + " ".join(str(p) for p in parts))


def save_report() -> None:
    REPORT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def world():
    return ues.get_editor_world()


def actors(cls=unreal.Actor):
    return [a for a in eas.get_all_level_actors() if isinstance(a, cls)]


def by_label(label: str):
    return next((a for a in eas.get_all_level_actors() if a.get_actor_label() == label), None)


def setp(obj, name: str, value) -> bool:
    try:
        obj.set_editor_property(name, value)
        return True
    except Exception as exc:  # noqa: BLE001 - logged; the report says what did not take
        log("could not set", type(obj).__name__, name, exc)
        report.setdefault("notSet", []).append(f"{type(obj).__name__}.{name}")
        return False


# ---------- geometry ---------------------------------------------------------

def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def vec(v) -> list[float]:
    return [v.x, v.y, v.z]


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination for a small square system."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [m[r][k] - f * m[c][k] for k in range(n + 1)]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_affine(src: list[list[float]], dst: list[list[float]]):
    """Least-squares dst = M src + t, returned as (M rows, t)."""
    rows = [s + [1.0] for s in src]
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
    out = []
    for k in range(3):
        atb = [sum(r[i] * d[k] for r, d in zip(rows, dst, strict=True)) for i in range(4)]
        out.append(solve(ata, atb))
    return [o[:3] for o in out], [o[3] for o in out]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def inverse(m):
    cols = [solve(m, [1.0 if i == j else 0.0 for i in range(3)]) for j in range(3)]
    return [[cols[j][i] for j in range(3)] for i in range(3)]


def det(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


# ---------- the georeference ---------------------------------------------------

def georeference():
    """Cesium's own default georeference (the tiles and the sky resolve to it), at the flat."""
    geo = unreal.CesiumGeoreference.get_default_georeference(world())
    o = SCENE["origin"]
    geo.set_origin_longitude_latitude_height(unreal.Vector(o["longitude"], o["latitude"], 0.0))
    world().get_world_settings().set_editor_property("enable_world_bounds_checks", False)
    return geo


class Frame:
    """Metres east/north/up of the scene origin (ellipsoid height h) to Unreal centimetres."""

    def __init__(self, geo):
        self.geo = geo
        o = SCENE["origin"]
        self.lat, self.lon = o["latitude"], o["longitude"]
        self.o = self._ue(0, 0, 0)
        # Unit steps of one metre (100 uu): east, north, up at the origin.
        self.e, self.n, self.u = (self._unit(sub(self._ue(*d), self.o))
                                  for d in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))

    @staticmethod
    def _unit(v):
        length = math.sqrt(sum(c * c for c in v))
        return [100.0 * c / length for c in v]

    def _ue(self, east, north, up):
        lat = self.lat + north / 111132.954
        lon = self.lon + east / (111412.84 * math.cos(math.radians(self.lat)))
        return vec(self.geo.transform_longitude_latitude_height_position_to_unreal(
            unreal.Vector(lon, lat, up)))

    def at(self, east, north, up) -> list[float]:
        return [self.o[i] + east * self.e[i] + north * self.n[i] + up * self.u[i] for i in range(3)]

    def direction(self, east, north, up) -> list[float]:
        v = [east * self.e[i] + north * self.n[i] + up * self.u[i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in v))
        return [c / length for c in v]


def scene_to_enu(x: float, y: float, z: float, floor: float) -> tuple[float, float, float]:
    b = SCENE["bearings"]
    bx, bmz = math.radians(b["plusX_endWall"]), math.radians(b["minusZ_balcony"])
    return (x * math.sin(bx) - z * math.sin(bmz), x * math.cos(bx) - z * math.cos(bmz), y + floor)


# ---------- the tiles and the sky ---------------------------------------------

def tileset():
    tiles = by_label("Google Photorealistic 3D Tiles")
    if tiles is None:
        tiles = eas.spawn_actor_from_class(unreal.Cesium3DTileset, unreal.Vector())
        tiles.set_actor_label("Google Photorealistic 3D Tiles")
    setp(tiles, "tileset_source", unreal.TilesetSource.FROM_CESIUM_ION)
    setp(tiles, "ion_asset_id", GOOGLE_TILES)
    setp(tiles, "ion_access_token", os.environ["CESIUM_ION_TOKEN"])
    setp(tiles, "maximum_screen_space_error", float(os.environ.get("FLAT02_SSE", "8")))
    return tiles


def sun_sky(frame: Frame):
    sky = next(iter(actors(unreal.CesiumSunSky)), None)
    if sky is None:
        sky = eas.spawn_actor_from_class(unreal.CesiumSunSky, unreal.Vector())
        sky.set_actor_label("Sun and sky")
    r = SCENE["render"]
    y, mo, d = (int(v) for v in r["date"].split("-"))
    hh, mm = (int(v) for v in r["time"].split(":"))
    for name, value in (("year", y), ("month", mo), ("day", d), ("solar_time", hh + mm / 60),
                        ("time_zone", float(r["utcOffset"])), ("use_daylight_saving_time", False)):
        setp(sky, name, value)
    # At the flat: a sky spawned from a script keeps the place Cesium starts at.
    o = SCENE["origin"]
    sky.set_actor_location(unreal.Vector(0, 0, 0), False, False)
    sky.get_editor_property("globe_anchor").move_to_longitude_latitude_height(
        unreal.Vector(o["longitude"], o["latitude"], 0.0))
    sky.update_sun()
    report["sunCesium"] = {"bearing": round(sky.get_editor_property("azimuth"), 2),
                           "elevation": round(sky.get_editor_property("elevation"), 2)}
    # The light's direction checked against the NOAA equations (site.json).
    az, el = math.radians(r["sun"]["bearing"]), math.radians(r["sun"]["elevation"])
    want = frame.direction(-math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el),
                           -math.sin(el))
    light = sky.get_editor_property("directional_light")
    got = vec(light.get_forward_vector())
    dot = sum(a * b for a, b in zip(want, got, strict=True))
    off = math.degrees(math.acos(max(-1, min(1, dot))))
    report["sun"] = {"expected": r["sun"], "cesiumOffDeg": round(off, 2)}
    if off > 0.5:
        # Cesium's sun model and NOAA's disagree: the light follows NOAA's.
        light.set_world_rotation(unreal.MathLibrary.make_rot_from_x(unreal.Vector(*want)),
                                 False, False)
        report["sun"]["overridden"] = True
    log("sun off by", round(off, 2), "deg")
    return sky


def post_process(adapt_from: float | None = None) -> None:
    """
    Exposure and white balance. Fixed at FLAT02_EV (EV100) for a still: auto exposure
    would lift the room and burn the street. For the walk, `adapt_from` lets it fall to
    that EV in the lamp-lit hall and climb back in the sunlit room, as a camera does.
    """
    ppv = by_label("Exposure")
    if ppv is None:
        ppv = eas.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
        ppv.set_actor_label("Exposure")
    ppv.set_editor_property("unbound", True)
    s = ppv.settings
    ev = float(os.environ.get("FLAT02_EV", "10.8"))
    pairs = {
        # EV100 limits (the project extends the luminance range, DefaultEngine.ini).
        "auto_exposure_method": unreal.AutoExposureMethod.AEM_HISTOGRAM,
        "auto_exposure_min_brightness": ev if adapt_from is None else adapt_from,
        "auto_exposure_max_brightness": ev,
        "auto_exposure_speed_up": 2.5, "auto_exposure_speed_down": 2.5,
        "auto_exposure_apply_physical_camera_exposure": False,
        "auto_exposure_bias": 0.0,
        # White balance as a camera in a sky-lit room would set it: warmer than daylight
        # film, or the walls come out blue.
        "white_temp": float(os.environ.get("FLAT02_WB", "6600")),
        "lumen_scene_lighting_quality": 2.0, "lumen_final_gather_quality": 2.0,
        "lumen_max_trace_distance": 20000.0,
        "lumen_ray_lighting_mode": unreal.LumenRayLightingModeOverride.HIT_LIGHTING_FOR_REFLECTIONS,
        "path_tracing_max_bounces": 8,
    }
    for k, v in pairs.items():
        try:
            s.set_editor_property(k, v)
            s.set_editor_property("override_" + k, True)
        except Exception as exc:  # noqa: BLE001
            log("pp", k, exc)
            report.setdefault("notSet", []).append(k)
    ppv.set_editor_property("settings", s)


# ---------- the flat --------------------------------------------------------------

def import_flat():
    """The flat's glTF through Interchange: one root actor ("Flat") with a mesh actor per node."""
    before = {a.get_path_name() for a in eas.get_all_level_actors()}
    mgr = unreal.InterchangeManager.get_interchange_manager_scripted()
    source = unreal.InterchangeManager.create_source_data(str(DATA / "flat.glb"))
    params = unreal.ImportAssetParameters()
    params.is_automated = True
    mgr.import_scene(MODEL_DIR, source, params)
    new = [a for a in eas.get_all_level_actors() if a.get_path_name() not in before]
    root = next(a for a in new if a.get_attach_parent_actor() is None)
    root.set_actor_label("Flat")
    report["importedActors"] = len(new)
    return root


def flat_parts() -> dict:
    return {a.get_actor_label(): a for a in by_label("Flat").get_attached_actors()}


def place_flat(frame: Frame, floor: float) -> None:
    """The flat where it stands: scene metres to east/north/up by the measured bearings."""
    root = by_label("Flat")
    root.set_actor_transform(unreal.Transform(), False, False)
    parts = flat_parts()
    # How the importer turned glTF's axes: fit the pins' imported centres.
    src, dst = [], []
    for name, centre in SCENE["pins"].items():
        origin, _ = parts[name].get_actor_bounds(False)
        src.append(centre)
        dst.append(vec(origin))
    m, t = fit_affine(src, dst)
    target = [frame.at(*scene_to_enu(*p, floor)) for p in src]
    a, c = fit_affine(src, target)
    r = matmul(a, inverse(m))
    origin_new = [c[i] - sum(r[i][k] * t[k] for k in range(3)) for i in range(3)]
    cols = [[r[i][j] for i in range(3)] for j in range(3)]
    rot = unreal.MathLibrary.make_rot_from_xz(unreal.Vector(*cols[0]), unreal.Vector(*cols[2]))
    root.set_actor_location_and_rotation(unreal.Vector(*origin_new), rot, False, False)
    errs = []
    for name, centre in SCENE["pins"].items():
        o, _ = parts[name].get_actor_bounds(False)
        want = frame.at(*scene_to_enu(*centre, floor))
        errs.append(math.sqrt(sum((x - y) ** 2 for x, y in zip(vec(o), want, strict=True))))
    report["placement"] = {
        "pins": len(src), "importAxes": [[round(x, 3) for x in row] for row in m],
        "rotationColumnLengths": [round(math.sqrt(sum(x * x for x in col)), 4) for col in cols],
        "det": round(det(r), 4), "floorAboveEllipsoidM": round(floor, 2),
        "pinErrorCm": round(max(errs), 2)}
    log("placed; pin error cm", report["placement"]["pinErrorCm"],
        "det", report["placement"]["det"])


def materials() -> None:
    """Glass and sheers that let the sun through; mirrors that mirror; the blinds up."""
    mel = unreal.MaterialEditingLibrary
    tools = unreal.AssetToolsHelpers.get_asset_tools()

    def make(name: str, colour, opacity: float, roughness: float, two_sided: bool):
        path = f"/Game/Flat02/{name}"
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            return unreal.load_asset(path)
        mat = tools.create_asset(name, "/Game/Flat02", unreal.Material, unreal.MaterialFactoryNew())
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        mat.set_editor_property("translucency_lighting_mode",
                                unreal.TranslucencyLightingMode.TLM_SURFACE)
        mat.set_editor_property("two_sided", two_sided)
        base = mel.create_material_expression(
            mat, unreal.MaterialExpressionConstant3Vector, -300, 0)
        base.set_editor_property("constant", unreal.LinearColor(*colour, 1))
        mel.connect_material_property(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
        for prop, value, y in ((unreal.MaterialProperty.MP_OPACITY, opacity, 150),
                               (unreal.MaterialProperty.MP_ROUGHNESS, roughness, 300),
                               (unreal.MaterialProperty.MP_SPECULAR, 0.5, 450)):
            k = mel.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, y)
            k.set_editor_property("r", value)
            mel.connect_material_property(k, "", prop)
        mel.recompile_material(mat)
        unreal.EditorAssetLibrary.save_loaded_asset(mat)
        return mat

    glass = make("M_Glass", (0.92, 0.95, 0.95), 0.06, 0.02, True)
    sheer = make("M_Sheer", (0.92, 0.9, 0.86), 0.45, 0.8, True)
    swapped = {"glass": 0, "sheer": 0, "mirror": 0}
    for comp in [c for a in actors()
                 for c in a.get_components_by_class(unreal.StaticMeshComponent)]:
        for i, slot in enumerate(comp.get_material_slot_names()):
            s = str(slot).lower()
            if "glass" in s and "frost" not in s and "beige" not in s:
                comp.set_material(i, glass)
                comp.set_editor_property("cast_shadow", False)
                swapped["glass"] += 1
            elif "sheer" in s:
                comp.set_material(i, sheer)
                swapped["sheer"] += 1
            elif "mirror" in s:
                mi = comp.get_material(i)
                if isinstance(mi, unreal.MaterialInstanceConstant):
                    for p in mel.get_scalar_parameter_names(mi):
                        if "rough" in str(p).lower():
                            mel.set_material_instance_scalar_parameter_value(mi, p, 0.0)
                        if "metal" in str(p).lower():
                            mel.set_material_instance_scalar_parameter_value(mi, p, 1.0)
                    swapped["mirror"] += 1
    blinds = by_label("blinds-east")
    if blinds:
        blinds.set_is_temporarily_hidden_in_editor(True)
        blinds.set_actor_hidden_in_game(True)
        blinds.set_actor_enable_collision(False)
    report["materials"] = swapped


def lamps() -> None:
    """The ceiling lamps on: a warm light under each lamp mesh (the hall has no window)."""
    root = by_label("Flat")
    for a in root.get_attached_actors():
        if a.get_actor_label().startswith("Lamp light"):
            eas.destroy_actor(a)
    count = 0
    for name, part in flat_parts().items():
        if not name.startswith("lamp-"):
            continue
        origin, extent = part.get_actor_bounds(False)
        up = root.get_actor_up_vector()
        drop = min(extent.x, extent.y, extent.z) + 12.0
        at = unreal.Vector(origin.x - up.x * drop, origin.y - up.y * drop, origin.z - up.z * drop)
        light = eas.spawn_actor_from_class(unreal.PointLight, at)
        light.set_actor_label(f"Lamp light {name[5:]}")
        c = light.point_light_component
        c.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        c.set_editor_property("intensity", float(os.environ.get("FLAT02_LAMP_LM", "1400")))
        c.set_editor_property("use_temperature", True)
        c.set_editor_property("temperature", 3000.0)
        c.set_editor_property("attenuation_radius", 700.0)
        c.set_editor_property("source_radius", 14.0)
        c.set_editor_property("soft_source_radius", 6.0)
        keep = unreal.AttachmentRule.KEEP_WORLD
        light.attach_to_actor(root, "", keep, keep, keep, False)
        count += 1
    report["lamps"] = count


# ---------- measuring in the tiles (in a play session: the editor's own world only
# updates Cesium while its viewport is in front on screen) ------------------------

def cesium_camera(game_world, eye, target, fov: float = 90.0) -> None:
    """Tell Cesium to load tiles for a view, whatever the viewport shows."""
    cam = unreal.CesiumCamera()
    cam.set_editor_property("viewport_size", unreal.Vector2D(1920, 1080))
    cam.set_editor_property("location", unreal.Vector(*eye))
    cam.set_editor_property("rotation", unreal.MathLibrary.find_look_at_rotation(
        unreal.Vector(*eye), unreal.Vector(*target)))
    cam.set_editor_property("field_of_view_degrees", fov)
    unreal.CesiumCameraManager.get_default_camera_manager(game_world).add_camera(cam)


def top_height(game_world, frame: Frame, east: float, north: float):
    """The highest surface at (east, north), metres above the ellipsoid, or None."""
    hit = unreal.SystemLibrary.line_trace_single(
        game_world, unreal.Vector(*frame.at(east, north, 400.0)),
        unreal.Vector(*frame.at(east, north, 100.0)), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True, [], unreal.DrawDebugTrace.NONE, True)
    if hit is None or not hit.to_tuple()[0]:
        return None
    p = vec(hit.to_tuple()[4])
    return sum((p[i] - frame.o[i]) * frame.u[i] for i in range(3)) / 1e4


# ---------- the cameras and the sequence -----------------------------------------

def scene_point(frame: Frame, p, floor: float):
    return frame.at(*scene_to_enu(*p, floor))


def cameras(frame: Frame, floor: float):
    cam = by_label("Still camera")
    if cam is None:
        cam = eas.spawn_actor_from_class(unreal.CineCameraActor, unreal.Vector())
        cam.set_actor_label("Still camera")
    s = SCENE["cameras"]["still"]
    eye, target = scene_point(frame, s["eye"], floor), scene_point(frame, s["target"], floor)
    rot = unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*eye), unreal.Vector(*target))
    cam.set_actor_location_and_rotation(unreal.Vector(*eye), rot, False, False)
    cc = cam.get_cine_camera_component()
    fb = cc.filmback
    fb.sensor_width, fb.sensor_height = 36.0, 20.25
    cc.set_editor_property("filmback", fb)
    cc.set_editor_property("current_focal_length", float(s["focalMm"]))
    cc.set_editor_property("current_aperture", APERTURE)
    focus = cc.focus_settings
    focus.focus_method = unreal.CameraFocusMethod.DISABLE
    cc.set_editor_property("focus_settings", focus)
    return cam, eye, target


# ---------- the build, step by step on the editor's ticks --------------------------------

class Again(Exception):
    """Raised by a step that is not ready yet: it runs again half a second later."""


class Steps:
    """Runs `steps` in order on the editor's ticks; a step returns seconds to wait after it."""

    def __init__(self, steps):
        self.steps, self.i, self.until, self.busy = steps, 0, 0.0, False
        self.handle = unreal.register_slate_post_tick_callback(self.tick)

    def tick(self, _dt) -> None:
        # A step that imports or saves pumps the editor's loop, which ticks this again.
        if self.busy or time.time() < self.until:
            return
        if self.i >= len(self.steps):
            unreal.unregister_slate_post_tick_callback(self.handle)
            return
        step = self.steps[self.i]
        self.busy = True
        try:
            wait = step()
        except Again:
            self.until = time.time() + 0.5
            return
        except Exception as exc:  # noqa: BLE001 - recorded, then straight to the last step
            report.setdefault("errors", []).append(f"{step.__name__}: {exc!r}")
            log("FAILED", step.__name__, repr(exc))
            save_report()
            self.i = len(self.steps) - 1
            return
        finally:
            self.busy = False
        self.i += 1
        self.until = time.time() + (wait or 0)


_polls: dict = {}


def tiles_ready(tiles, settle: float = 4.0, timeout: float = 240.0) -> float:
    """Raises Again until the tiles in view are loaded and have stayed so for `settle` s."""
    s = _polls.setdefault(id(tiles), {"start": time.time(), "last": -1.0, "since": time.time()})
    progress = tiles.get_editor_property("load_progress")
    if progress != s["last"]:
        s["last"], s["since"] = progress, time.time()
    if (progress >= 99 and time.time() - s["since"] > settle) or time.time() - s["start"] > timeout:
        report.setdefault("tileLoads", []).append(round(progress, 1))
        _polls.pop(id(tiles))
        return 1
    raise Again


def build() -> None:
    keep_awake()
    if not unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
        les.new_level(LEVEL)
    else:
        les.load_level(LEVEL)
        # From nothing each time: what an earlier build left is not built on.
        for actor in eas.get_all_level_actors():
            if type(actor).__name__ not in ("WorldSettings", "Brush", "WorldDataLayers"):
                eas.destroy_actor(actor)
    st: dict = {}
    storey = float(os.environ.get("FLAT02_STOREY_M", "2.86"))
    plinth = float(os.environ.get("FLAT02_PLINTH_M", "0.3"))

    def setup():
        st["frame"] = Frame(georeference())
        tileset()
        sun_sky(st["frame"])
        post_process()
        import_flat()
        materials()
        les.save_current_level()
        les.editor_play_simulate()
        return 3

    def aim():
        game = ues.get_game_world()
        if game is None:
            raise Again
        st["game"] = game
        st["tiles"] = unreal.GameplayStatics.get_all_actors_of_class(
            game, unreal.Cesium3DTileset)[0]
        f = st["frame"]
        cesium_camera(game, f.at(0, 0, 260), f.at(0.01, 0, 200))  # straight down on the plot
        cesium_camera(game, f.at(-20, -35, 215), f.at(0, 0, 203))
        return 2

    def tiles_loaded():
        return tiles_ready(st["tiles"], settle=4.0, timeout=120.0)

    def measure():
        f = st["frame"]
        # The ground under the flat: the building is newer than Google's imagery, the plot
        # is empty in the tiles. The median ignores the odd tree.
        xs = [p[0] for p in SCENE["outlineEastNorth"]]
        ys = [p[1] for p in SCENE["outlineEastNorth"]]
        heights = []
        for i in range(9):
            for j in range(9):
                e = min(xs) + (max(xs) - min(xs)) * i / 8
                n = min(ys) + (max(ys) - min(ys)) * j / 8
                h = top_height(st["game"], f, e, n)
                if h is not None:
                    heights.append(h)
        if len(heights) < 20:
            raise RuntimeError(f"only {len(heights)} ground hits in the tiles")
        heights.sort()
        ground = heights[len(heights) // 2]
        st["floor"] = ground + plinth + storey * SCENE["floorStorey"]
        report["ground"] = {
            "aboveEllipsoidM": round(ground, 2), "samples": len(heights),
            "spreadM": [round(heights[0], 2), round(heights[-1], 2)],
            "floorM": round(st["floor"], 2), "storeyM": storey, "plinthM": plinth,
            "assumed": ("storey = the scan's 2.56 m ceiling + 0.30 m slab; plinth 0.30 m; "
                        "not measured")}
        les.editor_request_end_play()
        return 3

    def place():
        if les.is_in_play_in_editor():
            raise Again
        place_flat(st["frame"], st["floor"])
        lamps()
        cameras(st["frame"], st["floor"])
        return 1

    def done():
        if les.is_in_play_in_editor():
            les.editor_request_end_play()
            raise Again
        les.save_current_level()
        unreal.EditorAssetLibrary.save_directory(
            "/Game/Flat02", only_if_is_dirty=True, recursive=True)
        report["built"] = time.strftime("%Y-%m-%d %H:%M")
        save_report()
        log("done")
        if os.environ.get("FLAT02_QUIT", "1") == "1":
            unreal.SystemLibrary.quit_editor()

    Steps([setup, aim, tiles_loaded, measure, place, done])


def keep_awake() -> None:
    """Realtime viewports. (That the editor works in the background is run.py's -ini switch.)"""
    try:
        les.editor_set_viewport_realtime(True)
    except Exception as exc:  # noqa: BLE001
        log("realtime:", exc)


def dev_loop() -> None:
    """
    FLAT02_MODE=dev: run data/.../unreal/cmd.py whenever it changes and write what it
    printed (or its traceback) to cmd.out, so a step can be tried in the open editor.
    """
    import contextlib
    import io
    import traceback

    cmd, out = DATA / "cmd.py", DATA / "cmd.out"
    state = {"mtime": cmd.stat().st_mtime if cmd.exists() else 0.0}
    scope = dict(globals())

    def tick(_dt) -> None:
        if not cmd.exists() or cmd.stat().st_mtime == state["mtime"]:
            return
        state["mtime"] = cmd.stat().st_mtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                exec(compile(cmd.read_text(encoding="utf-8"), str(cmd), "exec"), scope)  # noqa: S102
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=buffer)
        out.write_text(buffer.getvalue() + chr(10) + "#done " + str(state["mtime"]),
                       encoding="utf-8")

    keep_awake()
    dev_loop.handle = unreal.register_slate_post_tick_callback(tick)
    log("dev loop watching", cmd)


if __name__ == "__main__":
    if MODE == "build":
        build()
    elif MODE == "dev":
        dev_loop()
    else:
        import render  # noqa: F401  (scripts/unreal/render.py, same folder)
