"""
Movie Render Queue for flat-02, run inside the editor by in_editor.py
(FLAT02_MODE=render): a 4K still with the Path Tracer and a 30 s walk with
Lumen (hardware ray tracing), one after the other.

FLAT02_RENDER      still | walk | both (default both)
FLAT02_STILL_SIZE  default 3840x2160;  FLAT02_STILL_SPP  path-traced samples (default 256)
FLAT02_STILL_LUMEN 1 renders the still with Lumen instead (the fallback if 8 GB runs out)
FLAT02_WALK_SIZE   default 1920x1080;  FLAT02_WALK_SAMPLES  temporal samples (default 8)
Frames go to data/projects/flat-02/unreal/render/; run.py makes the video.
"""

from __future__ import annotations

import os
import time

from in_editor import (
    APERTURE,
    DATA,
    LEVEL,
    SCENE,
    Frame,
    by_label,
    eas,
    georeference,
    les,
    log,
    post_process,
    report,
    save_report,
    scene_point,
)

import unreal

OUT = DATA / "render"
WHAT = os.environ.get("FLAT02_RENDER", "both")
FPS = 30
WALK_SECONDS = 30
#: The lamp-lit hall, in EV100: where the walk's exposure may fall to.
WALK_EV_FLOOR = 7.0


def make_sequence(name: str, frames: int, keys: list[tuple[int, list[float], list[float], float]]):
    """A sequence of `frames` with one cine camera keyed at (frame, eye, target, focal mm)."""
    path = f"/Game/Flat02/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    seq = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, "/Game/Flat02", unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    seq.set_display_rate(unreal.FrameRate(FPS, 1))
    seq.set_playback_start(0)
    seq.set_playback_end(frames)
    cam = by_label(f"{name} camera")
    if cam is None:
        cam = eas.spawn_actor_from_class(unreal.CineCameraActor, unreal.Vector())
        cam.set_actor_label(f"{name} camera")
    cc = cam.get_cine_camera_component()
    fb = cc.filmback
    fb.sensor_width, fb.sensor_height = 36.0, 20.25
    cc.set_editor_property("filmback", fb)
    focus = cc.focus_settings
    focus.focus_method = unreal.CameraFocusMethod.DISABLE
    cc.set_editor_property("focus_settings", focus)
    cc.set_editor_property("current_focal_length", keys[0][3])
    cc.set_editor_property("current_aperture", APERTURE)
    binding = seq.add_possessable(cam)
    track = binding.add_track(unreal.MovieScene3DTransformTrack)
    section = track.add_section()
    section.set_range(0, frames)
    channels = section.get_all_channels()  # location xyz, rotation (roll, pitch, yaw), scale
    last_yaw = None
    for frame, eye, target, _ in keys:
        rot = unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*eye), unreal.Vector(*target))
        yaw = rot.yaw
        if last_yaw is not None:  # the short way round, never a spin through +-180
            while yaw - last_yaw > 180:
                yaw -= 360
            while yaw - last_yaw < -180:
                yaw += 360
        last_yaw = yaw
        for ch, value in zip(channels[:6], (*eye, 0.0, rot.pitch, yaw), strict=True):
            ch.add_key(unreal.FrameNumber(frame), float(value),
                       interpolation=unreal.MovieSceneKeyInterpolation.AUTO)
    cut = seq.add_track(unreal.MovieSceneCameraCutTrack).add_section()
    cut.set_range(0, frames)
    cut.set_camera_binding_id(seq.get_binding_id(binding))
    unreal.EditorAssetLibrary.save_loaded_asset(seq)
    return seq


def add_job(queue, seq, name: str, size: str, path_tracer: bool, spatial: int,
            temporal: int) -> None:
    job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
    job.job_name = name
    job.map = unreal.SoftObjectPath(LEVEL)
    job.sequence = unreal.SoftObjectPath(seq.get_path_name())
    cfg = job.get_configuration()
    out = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out.output_directory = unreal.DirectoryPath(str(OUT / name))
    out.file_name_format = "{sequence_name}.{frame_number}"
    width, height = (int(v) for v in size.split("x"))
    out.output_resolution = unreal.IntPoint(width, height)
    out.zero_pad_frame_numbers = 4
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPass_PathTracer if path_tracer
                                     else unreal.MoviePipelineDeferredPassBase)
    aa = cfg.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
    aa.spatial_sample_count = spatial
    aa.temporal_sample_count = temporal
    aa.override_anti_aliasing = True
    aa.anti_aliasing_method = (unreal.AntiAliasingMethod.AAM_NONE if path_tracer or temporal >= 8
                               else unreal.AntiAliasingMethod.AAM_TSR)
    aa.engine_warm_up_count = 120  # Lumen and the exposure settle before the first frame
    aa.render_warm_up_count = 120
    aa.render_warm_up_frames = True
    console = cfg.find_or_add_setting_by_class(unreal.MoviePipelineConsoleVariableSetting)
    console.add_or_update_console_variable("r.MotionBlurQuality", 0.0)
    console.add_or_update_console_variable("r.ScreenPercentage", 100.0)
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG if name == "still"
                                     else unreal.MoviePipelineImageSequenceOutput_JPG)


def still_job(queue, frame: Frame, floor: float) -> None:
    post_process()  # one fixed exposure
    s = SCENE["cameras"]["still"]
    seq = make_sequence("Still", 1, [(0, scene_point(frame, s["eye"], floor),
                                      scene_point(frame, s["target"], floor), float(s["focalMm"]))])
    lumen = os.environ.get("FLAT02_STILL_LUMEN", "0") == "1"
    add_job(queue, seq, "still", os.environ.get("FLAT02_STILL_SIZE", "3840x2160"), not lumen,
            1 if lumen else int(os.environ.get("FLAT02_STILL_SPP", "256")), 16 if lumen else 1)


def walk_job(queue, frame: Frame, floor: float) -> None:
    post_process(adapt_from=WALK_EV_FLOOR)
    keys = [(round(k["t"] * FPS), scene_point(frame, k["eye"], floor),
             scene_point(frame, k["look"], floor), 16.0) for k in SCENE["cameras"]["walk"]]
    seq = make_sequence("Walk", FPS * WALK_SECONDS, keys)
    add_job(queue, seq, "walk", os.environ.get("FLAT02_WALK_SIZE", "1920x1080"), False, 1,
            int(os.environ.get("FLAT02_WALK_SAMPLES", "8")))


def run() -> None:
    les.load_level(LEVEL)
    frame = Frame(georeference())
    floor = report["ground"]["floorM"]
    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    todo = [n for n in ("still", "walk") if WHAT in (n, "both")]
    report["render"] = {}
    state = {"world": None, "started": 0.0, "name": ""}

    # Cesium only waits for its tiles (its "movie capture" mode) for sequences that are in
    # the level when play begins; the queue starts its own afterwards. So: as soon as a
    # play world exists, tell every tileset in it that a movie is being made.
    def capture_mode(_dt) -> None:
        game = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
        if game is None or state["world"] == game:
            return
        state["world"] = game
        for tiles in unreal.GameplayStatics.get_all_actors_of_class(game, unreal.Cesium3DTileset):
            tiles.play_movie_sequencer()

    def start_next() -> None:
        if not todo:
            unreal.unregister_slate_post_tick_callback(run.tick)
            save_report()
            log("render finished")
            if os.environ.get("FLAT02_QUIT", "1") == "1":
                unreal.SystemLibrary.quit_editor()
            return
        state["name"] = todo.pop(0)
        queue = subsystem.get_queue()
        queue.delete_all_jobs()
        (still_job if state["name"] == "still" else walk_job)(queue, frame, floor)
        les.save_current_level()
        state["started"] = time.time()
        executor = unreal.MoviePipelinePIEExecutor()
        executor.on_executor_finished_delegate.add_callable_unique(finished)
        executor.on_executor_errored_delegate.add_callable_unique(errored)
        run.keep = executor  # not garbage collected while it renders
        subsystem.render_queue_with_executor_instance(executor)

    def finished(_executor, success) -> None:
        report["render"][state["name"]] = {"success": bool(success),
                                           "seconds": round(time.time() - state["started"])}
        save_report()
        log("rendered", state["name"], success)
        start_next()

    def errored(_executor, _pipeline, _fatal, text) -> None:
        report.setdefault("errors", []).append(f"render {state['name']}: {text}")
        save_report()

    run.tick = unreal.register_slate_post_tick_callback(capture_mode)
    start_next()


run()
