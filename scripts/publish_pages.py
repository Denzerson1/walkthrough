"""
Publish the static site to GitHub Pages.

The site carries the flat's scene files, which are too large for git
(CLAUDE.md, constraint 2), so GitHub does not build it: this archives the
export as the one asset of the `site` release and runs the Pages workflow,
which deploys that archive as it is.

Run:  pnpm publish:pages   (after pnpm build:pages)
"""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "apps" / "web" / "dist"
ARCHIVE = REPO / "data" / "site.tar.gz"
TAG = "site"


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], cwd=REPO, check=check, capture_output=True, text=True)


def main() -> None:
    if not (DIST / "api" / "projects.json").is_file():
        raise SystemExit("Export the site first: pnpm build:pages")
    with tarfile.open(ARCHIVE, "w:gz") as tar:
        for path in sorted(DIST.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(DIST).as_posix())

    if gh("release", "view", TAG, check=False).returncode != 0:
        gh("release", "create", TAG, "--title", "Site", "--latest=false", "--notes",
           "The GitHub Pages site, deployed by .github/workflows/pages.yml and "
           "replaced by `pnpm publish:pages`.")
    gh("release", "upload", TAG, str(ARCHIVE), "--clobber")
    gh("workflow", "run", "pages.yml")
    print(f"Uploaded {ARCHIVE.stat().st_size / 1e6:.0f} MB; deploying: gh run watch")


if __name__ == "__main__":
    main()
