"""
Runs inside the Unreal editor (unreal/flat02), started by scripts/unreal/run.py.

FLAT02_MODE=build   the level: Cesium georeference at the flat, Google's tiles
                    streamed (token from the environment, never saved in git),
                    the sun for the date and time, the flat imported and placed
                    by the measured bearings and floor height, the flat's own
                    building clipped out of the tiles where the flat is,
                    glass and sheers; then measurements and quick looks.
FLAT02_MODE=render  Movie Render Queue: the 4K still and the 30 s walk.

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
REPORT = DATA / "report.json"
report: dict = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}

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
        atb = [sum(r[i] * d[k] for r, d in zip(rows, dst)) for i in range(4)]
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
    geo = next(iter(actors(unreal.CesiumGeoreference)), None)
    if geo is None:
        geo = eas.spawn_actor_from_class(unreal.CesiumGeoreference, unreal.Vector())
        geo.set_actor_label("Georeference")
    o = SCENE["origin"]
    geo.set_origin_longitude_latitude_height(unreal.Vector(o["longitude"], o["latitude"], 0.0))
    return geo


class Frame:
    """Metres east/north/up of the scene origin (ellipsoid height h) to Unreal centimetres."""

    def __init__(self, geo):
        self.geo = geo
        o = SCENE["origin"]
        self.lat, self.lon = o["latitude"], o["longitude"]
        self.o = self._ue(0, 0, 0)
        self.e = sub(self._ue(1, 0, 0), self.o)
        self.n = sub(self._ue(0, 1, 0), self.o)
        self.u = sub(self._ue(0, 0, 1), self.o)

    def _ue(self, east, north, up):
        lat = self.lat + north / 111132.954
        lon = self.lon + east / (111412.84 * math.cos(math.radians(self.lat)))
        p = self.geo.transform_longitude_latitude_height_position_to_unreal(unreal.Vector(lon, lat, up))
        return vec(p)

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
                        ("time_zone", float(r["utcOffset"])), ("use_daylight_saving", False)):
        setp(sky, name, value)
    sky.update_sun()
    # The light's direction checked against the NOAA equations (site.json).
    az, el = math.radians(r["sun"]["bearing"]), math.radians(r["sun"]["elevation"])
    want = frame.direction(-math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), -math.sin(el))
    light = next(c for c in sky.get_components_by_class(unreal.DirectionalLightComponent))
    got = vec(light.get_forward_vector())
    off = math.degrees(math.acos(max(-1, min(1, sum(a * b for a, b in zip(want, got))))))
    report["sun"] = {"expected": r["sun"], "cesiumOffDeg": round(off, 2)}
    if off > 0.5:
        # Cesium's sun model and NOAA's disagree: the light follows NOAA's.
        light.set_world_rotation(unreal.MathLibrary.make_rot_from_x(unreal.Vector(*want)), False, False)
        report["sun"]["overridden"] = True
    log("sun off by", round(off, 2), "deg")
    return sky


def post_process():
    ppv = by_label("Exposure")
    if ppv is None:
        ppv = eas.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
        ppv.set_actor_label("Exposure")
    ppv.set_editor_property("unbound", True)
    s = ppv.settings
    pairs = {
        "auto_exposure_method": unreal.AutoExposureMethod.AEM_HISTOGRAM,
        "auto_exposure_min_brightness": float(os.environ.get("FLAT02_EV_MIN", "7")),
        "auto_exposure_max_brightness": float(os.environ.get("FLAT02_EV_MAX", "12")),
        "auto_exposure_bias": float(os.environ.get("FLAT02_EV_BIAS", "0.5")),
        "lumen_scene_lighting_quality": 2.0, "lumen_final_gather_quality": 2.0,
        "lumen_max_trace_distance": 20000.0, "lumen_ray_lighting_mode": unreal.LumenRayLightingModeOverride.HIT_LIGHTING_FOR_REFLECTIONS,
        "path_tracing_max_bounces": 8, "path_tracing_samples_per_pixel": int(os.environ.get("FLAT02_SPP", "256")),
    }
    for k, v in pairs.items():
        try:
            s.set_editor_property(k, v)
            s.set_editor_property("override_" + k, True)
        except Exception as exc:  # noqa: BLE001
            log("pp", k, exc)
    ppv.set_editor_property("settings", s)


# ---------- the flat --------------------------------------------------------------

def import_flat() -> None:
    before = {a.get_path_name() for a in eas.get_all_level_actors()}
    mgr = unreal.InterchangeManager.get_interchange_manager_scripted()
    source = unreal.InterchangeManager.create_source_data(str(DATA / "flat.glb"))
    params = unreal.ImportAssetParameters()
    params.is_automated = True
    mgr.import_scene(MODEL_DIR, source, params)
    report["importedActors"] = len([a for a in eas.get_all_level_actors() if a.get_path_name() not in before])


def flat_actors():
    skip = (unreal.CesiumGeoreference, unreal.Cesium3DTileset, unreal.CesiumSunSky, unreal.PostProcessVolume,
            unreal.CameraActor, unreal.CesiumCartographicPolygon)
    return [a for a in eas.get_all_level_actors()
            if not isinstance(a, skip) and a.get_actor_label() not in ("Flat",)
            and a.get_attach_parent_actor() is None and a.get_folder_path() != "Cesium"
            and type(a).__name__ not in ("CesiumCreditSystem", "CesiumCameraManager", "WorldSettings", "Brush")
            and (a.get_components_by_class(unreal.StaticMeshComponent) or type(a).__name__ == "Actor")]


def place_flat(frame: Frame, floor: float) -> None:
    root = by_label("Flat")
    if root is None:
        root = eas.spawn_actor_from_class(unreal.Actor, unreal.Vector())
        root.set_actor_label("Flat")
        if root.root_component is None:
            root.set_editor_property("root_component", unreal.SceneComponent(root, "Root"))
    root.set_actor_transform(unreal.Transform(), False, False)
    children = [a for a in flat_actors() if a != root]
    for a in children:
        a.attach_to_actor(root, "", unreal.AttachmentRule.KEEP_WORLD, unreal.AttachmentRule.KEEP_WORLD,
                          unreal.AttachmentRule.KEEP_WORLD, False)
    labels = {a.get_actor_label(): a for a in root.get_attached_actors()}
    # How the importer turned glTF's axes: fit the pins' imported centres.
    src, dst = [], []
    for name, centre in SCENE["pins"].items():
        a = labels.get(name) or next((v for k, v in labels.items() if k.startswith(name)), None)
        if a is None:
            continue
        origin, _ = a.get_actor_bounds(False)
        src.append(centre)
        dst.append(vec(origin))
    m, t = fit_affine(src, dst)
    # Where the scene should go: metres east/north/up of the origin, floor `floor` up.
    target = []
    for p in src:
        target.append(frame.at(*scene_to_enu(*p, floor)))
    a, c = fit_affine(src, target)
    r = matmul(a, inverse(m))
    origin_new = [c[i] - sum(r[i][k] * t[k] for k in range(3)) for i in range(3)]
    cols = [[r[i][j] for i in range(3)] for j in range(3)]
    norms = [math.sqrt(sum(x * x for x in col)) for col in cols]
    report["placement"] = {"pins": len(src), "importAxes": [[round(x, 3) for x in row] for row in m],
                           "rotationColumnNorms": [round(n, 4) for n in norms], "det": round(det(r), 4),
                           "floorAboveOriginM": floor}
    rot = unreal.MathLibrary.make_rot_from_xz(unreal.Vector(*cols[0]), unreal.Vector(*cols[2]))
    root.set_actor_location_and_rotation(unreal.Vector(*origin_new), rot, False, False)
    # Check: every pin where it should be.
    errs = []
    for name, centre in SCENE["pins"].items():
        act = labels.get(name) or next((v for k, v in labels.items() if k.startswith(name)), None)
        if act:
            o, _ = act.get_actor_bounds(False)
            want = frame.at(*scene_to_enu(*centre, floor))
            errs.append(math.sqrt(sum((x - y) ** 2 for x, y in zip(vec(o), want))))
    report["placement"]["pinErrorCm"] = round(max(errs), 2) if errs else None
    log("placed; pin error cm", report["placement"]["pinErrorCm"], "det", report["placement"]["det"])


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
        mat.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE)
        mat.set_editor_property("two_sided", two_sided)
        base = mel.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -300, 0)
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
    for comp in [c for a in actors() for c in a.get_components_by_class(unreal.StaticMeshComponent)]:
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


# ---------- the flat's own building out of the tiles --------------------------

def clip(frame: Frame, tiles, ground: float) -> None:
    poly = next(iter(actors(unreal.CesiumCartographicPolygon)), None)
    if poly is None:
        poly = eas.spawn_actor_from_class(unreal.CesiumCartographicPolygon, unreal.Vector(*frame.o))
        poly.set_actor_label("Flat outline (clipped from the tiles)")
    spline = poly.get_components_by_class(unreal.SplineComponent)[0]
    pts = [unreal.Vector(*frame.at(e, n, ground)) for e, n in SCENE["clipEastNorth"]]
    spline.set_spline_points(pts, unreal.SplineCoordinateSpace.WORLD, True)
    spline.set_closed_loop(True)
    overlays = tiles.get_components_by_class(unreal.CesiumPolygonRasterOverlay)
    if not overlays:
        sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        handles = sds.k2_gather_subobject_data_for_instance(tiles)
        params = unreal.AddNewSubobjectParams(parent_handle=handles[0],
                                              new_class=unreal.CesiumPolygonRasterOverlay)
        sds.add_new_subobject(params)
        overlays = tiles.get_components_by_class(unreal.CesiumPolygonRasterOverlay)
    overlay = overlays[0]
    setp(overlay, "polygons", [poly])
    setp(overlay, "material_layer_key", "Clipping")
    report["clip"] = {"points": len(pts), "overlay": bool(overlays)}


# ---------- measuring in the tiles ------------------------------------------------

def trace_down(frame: Frame, east: float, north: float, top: float = 80.0) -> list[float]:
    """Every surface straight down at (east, north), metres above the origin's ellipsoid."""
    hits, z = [], top
    for _ in range(12):
        start, end = frame.at(east, north, z), frame.at(east, north, -150.0)
        hit = unreal.SystemLibrary.line_trace_single(world(), unreal.Vector(*start), unreal.Vector(*end),
                                                     unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [],
                                                     unreal.DrawDebugTrace.NONE, True)
        if hit is None:
            break
        t = hit.to_tuple()
        if not t[0]:
            break
        p = vec(t[5])
        up = sum((p[i] - frame.o[i]) * frame.u[i] for i in range(3)) / sum(c * c for c in frame.u)
        hits.append(round(up, 2))
        z = up - 0.3
    return hits


def look(name: str, eye, target, fov: float = 70.0) -> None:
    rot = unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*eye), unreal.Vector(*target))
    ues.set_level_viewport_camera_info(unreal.Vector(*eye), rot)
    report.setdefault("looks", []).append(name)


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
    cam.set_actor_location_and_rotation(
        unreal.Vector(*eye), unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*eye), unreal.Vector(*target)),
        False, False)
    cc = cam.get_cine_camera_component()
    fb = cc.filmback
    fb.sensor_width, fb.sensor_height = 36.0, 20.25
    cc.set_editor_property("filmback", fb)
    cc.set_editor_property("current_focal_length", float(s["focalMm"]))
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
        self.steps, self.i, self.until = steps, 0, 0.0
        self.handle = unreal.register_slate_post_tick_callback(self.tick)

    def tick(self, _dt) -> None:
        if time.time() < self.until:
            return
        if self.i >= len(self.steps):
            unreal.unregister_slate_post_tick_callback(self.handle)
            return
        step = self.steps[self.i]
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
        self.i += 1
        self.until = time.time() + (wait or 0)


_polls: dict = {}


def tiles_ready(tiles, settle: float = 4.0, timeout: float = 240.0) -> float:
    """Raises Again until the tiles in view are loaded and have stayed so for `settle` s."""
    s = _polls.setdefault(id(tiles), {"start": time.time(), "last": -1.0, "since": time.time()})
    progress = tiles.get_load_progress()
    if progress != s["last"]:
        s["last"], s["since"] = progress, time.time()
    if (progress >= 100 and time.time() - s["since"] > settle) or time.time() - s["start"] > timeout:
        report.setdefault("tileLoads", []).append(round(progress, 1))
        _polls.pop(id(tiles))
        return 1
    raise Again


def screenshot(name: str) -> None:
    """The active viewport, to Saved/Screenshots (run.py collects them in order)."""
    unreal.SystemLibrary.execute_console_command(world(), "HighResShot 1920x1080")
    report.setdefault("screenshots", []).append(name)


def build() -> None:
    perf = unreal.get_default_object(unreal.EditorPerformanceSettings)
    perf.set_editor_property("throttle_cpu_when_not_foreground", False)
    if not unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
        les.new_level(LEVEL)
    else:
        les.load_level(LEVEL)
    st: dict = {}

    def setup():
        st["geo"] = georeference()
        st["frame"] = Frame(st["geo"])
        st["tiles"] = tileset()
        sun_sky(st["frame"])
        post_process()
        f = st["frame"]
        # From the street south-west of the balcony, looking at the building.
        look("outside", f.at(-25, -45, 30), f.at(0, 0, 4))
        return 2

    def tiles_loaded_outside():
        return tiles_ready(st["tiles"])

    def measure():
        f = st["frame"]
        # Ground: around the building, outside its footprint; the balcony's slabs.
        samples = {
            "street-south": trace_down(f, -2, -14),
            "garden-west": trace_down(f, -14, 4),
            "north": trace_down(f, 0, 12),
            "balcony": trace_down(f, *SCENE["balconyEastNorth"]) if "balconyEastNorth" in SCENE else None,
            "roof": trace_down(f, 0, 0),
        }
        report["traces"] = samples
        grounds = [s[-1] for k, s in samples.items() if s and k in ("street-south", "garden-west", "north")]
        ground = sorted(grounds)[len(grounds) // 2] if grounds else 0.0
        st["ground"] = ground
        storey = float(os.environ.get("FLAT02_STOREY_M", "2.86"))
        plinth = float(os.environ.get("FLAT02_PLINTH_M", "0.3"))
        st["floor"] = ground + plinth + storey * SCENE["floorStorey"]
        report["ground"] = {"aboveEllipsoidM": ground, "floorM": st["floor"], "storeyM": storey,
                            "plinthM": plinth}
        save_report()
        screenshot("outside_before")
        return 3

    def flat():
        import_flat()
        return 5

    def place():
        place_flat(st["frame"], st["floor"])
        materials()
        clip(st["frame"], st["tiles"], st["ground"] - 5)
        cam, eye, target = cameras(st["frame"], st["floor"])
        st["still"] = (eye, target)
        f = st["frame"]
        look("outside", f.at(-25, -45, st["floor"] + 20), f.at(2, 0, st["floor"]))
        return 3

    def outside_shot():
        screenshot("outside_after")
        eye, target = st["still"]
        look("still", eye, target)
        return 3

    def tiles_loaded_inside():
        return tiles_ready(st["tiles"])

    def inside_shot():
        screenshot("still_preview")
        return 6

    def done():
        les.save_current_level()
        unreal.EditorAssetLibrary.save_directory("/Game/Flat02", only_if_is_dirty=True, recursive=True)
        report["built"] = time.strftime("%Y-%m-%d %H:%M")
        save_report()
        log("done")
        if os.environ.get("FLAT02_QUIT", "1") == "1":
            unreal.SystemLibrary.quit_editor()

    Steps([setup, tiles_loaded_outside, measure, flat, place, outside_shot, tiles_loaded_inside,
           inside_shot, done])


if MODE == "build":
    build()
else:
    import render  # noqa: F401  (scripts/unreal/render.py, same folder)
