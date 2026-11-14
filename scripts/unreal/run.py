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
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "unreal" / "flat02" / "flat02.uproject"
DATA = REPO / "data" / "projects" / "flat-02" / "unreal"
EDITOR = Path(os.environ.get("UE_ROOT", r"C:\Program Files\Epic Games\UE_5.8")) / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
SHOTS = REPO / "unreal" / "flat02" / "Saved" / "Screenshots" / "WindowsEditor"


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
           "PYTHONPATH": str(Path(__file__).parent)}
    started = time.time()
    script = Path(__file__).with_name("in_editor.py").as_posix()
    subprocess.run([str(EDITOR), str(PROJECT), f"-ExecutePythonScript={script}", "-log=flat02.log",
                    "-nosplash", "-NoLoadStartupPackages"], env=env, check=False)
    report = json.loads((DATA / "report.json").read_text(encoding="utf-8"))
    taken = sorted((p for p in SHOTS.glob("*.png") if p.stat().st_mtime > started),
                   key=lambda p: p.stat().st_mtime) if SHOTS.exists() else []
    (DATA / "shots").mkdir(exist_ok=True)
    for name, path in zip(report.get("screenshots", []), taken):
        shutil.copy2(path, DATA / "shots" / f"{name}.png")
    print(json.dumps({k: v for k, v in report.items() if k != "screenshots"}, indent=1)[:4000])
    print(f"{len(taken)} screenshots -> {DATA / 'shots'}; {time.time() - started:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build")
