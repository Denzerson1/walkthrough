"""
Render a splat PLY from given cameras with gsplat, and score the renders
against the photos taken there. Runs in the trainer's venv (torch, gsplat,
fused_ssim), not the project's: `pipeline compare` writes a job file and
calls this with GSPLAT_PYTHON.

    python render_eval.py job.json

job.json: {"ply": ..., "out_dir": ..., "images_dir": ... | null,
           "sh_degree": 3, "cameras": [{"name", "width", "height",
           "K": [fx, fy, cx, cy], "c2w": 4x4 (OpenCV axes)}]}

Writes <out_dir>/<name>.png renders and metrics.json (PSNR, SSIM per image
and their means). Images are compared at the camera's resolution.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import torch
from fused_ssim import fused_ssim
from gsplat.rendering import rasterization


def read_ply(path: Path) -> dict[str, torch.Tensor]:
    with open(path, "rb") as fh:
        header = b""
        while b"end_header" not in header:
            header += fh.readline()
        fields, count = [], 0
        for line in header.decode("ascii").splitlines():
            if line.startswith("element vertex"):
                count = int(line.split()[-1])
            elif line.startswith("property float"):
                fields.append(line.split()[-1])
        data = np.frombuffer(fh.read(count * 4 * len(fields)),
                             dtype=[(f, "<f4") for f in fields], count=count)
    rest = sorted((f for f in fields if f.startswith("f_rest_")), key=lambda f: int(f[7:]))
    k = len(rest) // 3
    dc = np.stack([data[f"f_dc_{i}"] for i in range(3)], axis=1)[:, None, :]
    sh = dc
    if k:
        channel_major = np.stack([data[f] for f in rest], axis=1).reshape(count, 3, k)
        sh = np.concatenate([dc, channel_major.transpose(0, 2, 1)], axis=1)

    def col(*names):
        return np.stack([data[n] for n in names], axis=1)

    dev = "cuda"
    return {
        "means": torch.tensor(col("x", "y", "z"), device=dev),
        "quats": torch.tensor(col("rot_0", "rot_1", "rot_2", "rot_3"), device=dev),
        "scales": torch.exp(torch.tensor(col("scale_0", "scale_1", "scale_2"), device=dev)),
        "opacities": torch.sigmoid(torch.tensor(np.asarray(data["opacity"]), device=dev)),
        "sh": torch.tensor(sh, device=dev),
    }


def main(job_path: str) -> None:
    job = json.loads(Path(job_path).read_text())
    out = Path(job["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    splats = read_ply(Path(job["ply"]))
    degree = int(round(np.sqrt(splats["sh"].shape[1]))) - 1
    degree = min(degree, int(job.get("sh_degree", degree)))
    metrics = {}
    for cam in job["cameras"]:
        fx, fy, cx, cy = cam["K"]
        K = torch.tensor([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]], device="cuda")[None]
        viewmat = torch.linalg.inv(torch.tensor(cam["c2w"], device="cuda", dtype=torch.float32))
        with torch.no_grad():
            colours, _, _ = rasterization(
                splats["means"], splats["quats"], splats["scales"], splats["opacities"],
                splats["sh"], viewmat[None], K, cam["width"], cam["height"],
                sh_degree=degree, packed=True,
            )
        image = colours[0].clamp(0, 1)
        png = (image.cpu().numpy() * 255).round().astype(np.uint8)
        imageio.imwrite(out / f"{Path(cam['name']).stem}.png", png)
        if job.get("images_dir"):
            truth_path = Path(job["images_dir"]) / cam["name"]
            if truth_path.is_file():
                truth = imageio.imread(truth_path)[..., :3]
                if truth.shape[:2] != png.shape[:2]:
                    continue
                gt = torch.tensor(truth, device="cuda", dtype=torch.float32) / 255.0
                mse = torch.mean((image - gt) ** 2).item()
                ssim = fused_ssim(image.permute(2, 0, 1)[None], gt.permute(2, 0, 1)[None],
                                  padding="valid").item()
                metrics[cam["name"]] = {"psnr": round(-10 * np.log10(max(mse, 1e-10)), 3),
                                        "ssim": round(ssim, 4)}
    summary = {}
    if metrics:
        summary = {"psnr": round(float(np.mean([m["psnr"] for m in metrics.values()])), 3),
                   "ssim": round(float(np.mean([m["ssim"] for m in metrics.values()])), 4),
                   "images": len(metrics)}
    (out / "metrics.json").write_text(json.dumps({"mean": summary, "per_image": metrics},
                                                 indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv[1])
