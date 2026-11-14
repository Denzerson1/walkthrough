"""
Build or render flat-02 in Unreal (docs/FLAT02_UNREAL.md).

    python scripts/unreal/prepare_flat.py          # the model and scene.json
    python scripts/unreal/run.py build             # the level, measurements, quick looks
    python scripts/unreal/run.py render            # the 4K still and the 30 s video

Starts the editor on unreal/flat02 with scripts/unreal/in_editor.py and
waits for it to quit. The Cesium ion token is read from .env and handed to
the editor in its environment only. Screenshots the editor took are copied
to data/projects/flat-02/unreal/shots/ under the names the script gave them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "unreal" / "flat02" / "flat02.uproject"
DATA = REPO / "data" / "projects" / "flat-02" / "unreal"
EDITOR = (Path(os.environ.get("UE_ROOT", r"C:\Program Files\Epic Games\UE_5.8"))
          / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe")
TIMEOUT_S = int(os.environ.get("FLAT02_TIMEOUT_S", "1500"))


def env_file() -> dict:
    out = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main(mode: str) -> None:
    token = env_file().get("CESIUM_ION_TOKEN")
    if not token:
        raise SystemExit("Set CESIUM_ION_TOKEN in .env")
    env = {**os.environ, "FLAT02_MODE": mode, "FLAT02_DATA": str(DATA), "CESIUM_ION_TOKEN": token,
           "UE_PYTHONPATH": str(Path(__file__).parent)}
    started = time.time()
    script = Path(__file__).with_name("in_editor.py").as_posix()
    # ExecCmds, not -ExecutePythonScript: that one quits the editor as soon as the
    # script returns, and the build runs step by step on the editor's ticks.
    command = (f'"{EDITOR}" "{PROJECT}" -ExecCmds="py {script}" -log=flat02.log -nosplash '
               f"-NoLoadStartupPackages "
               # Without this the editor stops ticking the world when its window is not in
               # front, and no tile ever loads.
               f"-ini:EditorSettings:[/Script/UnrealEd.EditorPerformanceSettings]:"
               f"bThrottleCPUWhenNotForeground=False")
    try:
        subprocess.run(command, env=env, check=False, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"The editor did not finish in {TIMEOUT_S} s and was stopped.")
    path = DATA / "report.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "errors": ["no report: the script did not run; see unreal/flat02/Saved/Logs/flat02.log"]}
    if mode == "render" and not report.get("errors"):
        report["outputs"] = finish(report)
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1)[:5000])
    print(f"{mode}: {time.time() - started:.0f} s")


def credit(size: tuple[int, int], text: str):
    """Google's attribution as a transparent layer for a frame of `size`, bottom right."""
    from PIL import Image, ImageDraw, ImageFont

    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    px = max(14, size[1] // 60)
    try:
        font = ImageFont.truetype("arial.ttf", px)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    x, y = size[0] - (box[2] - box[0]) - px, size[1] - (box[3] - box[1]) - px
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 200))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 235))
    return layer


def finish(report: dict) -> dict:
    """
    out/flat02_still.png and out/flat02_walk.mp4, each carrying the attribution Google
    sends with its tiles (Map Tiles API policies). The video is encoded by ffmpeg in WSL.
    """
    from PIL import Image

    sys.path.insert(0, str(REPO / "scripts"))
    import check_3d_tiles

    scene = json.loads((DATA / "scene.json").read_text(encoding="utf-8"))
    text = "Outside: " + check_3d_tiles.attribution(scene["origin"]["latitude"],
                                                     scene["origin"]["longitude"])
    out = DATA / "out"
    out.mkdir(exist_ok=True)
    made = {"attribution": text}
    stills = sorted((DATA / "render" / "still").glob("*.png"))
    if "still" in report.get("render", {}) and stills:
        image = Image.open(stills[-1]).convert("RGBA")
        image.alpha_composite(credit(image.size, text))
        image.convert("RGB").save(out / "flat02_still.png")
        made["still"] = [str(out / "flat02_still.png"), list(image.size)]
    frames = sorted((DATA / "render" / "walk").glob("*.jpeg"))
    if "walk" in report.get("render", {}) and frames:
        size = Image.open(frames[0]).size
        credit(size, text).save(out / "credit.png")

        def wsl(p: Path) -> str:
            return "/mnt/" + p.drive[0].lower() + p.as_posix()[2:]

        pattern = wsl(DATA / "render" / "walk") + "/Walk.%04d.jpeg"
        video = out / "flat02_walk.mp4"
        done = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "-u", "root", "--", "ffmpeg", "-y", "-loglevel", "error",
             "-framerate", "30", "-i", pattern, "-i", wsl(out / "credit.png"),
             "-filter_complex", "[0][1]overlay=0:0,format=yuv420p", "-c:v", "libx264", "-crf", "17",
             "-preset", "slow", "-movflags", "+faststart", wsl(video)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        if done.returncode:
            made["walkError"] = done.stderr[-500:]
        else:
            made["walk"] = [str(video), list(size), len(frames),
                            round(video.stat().st_size / 1e6, 1)]
    return made


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build")
