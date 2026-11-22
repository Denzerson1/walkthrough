"""
Single-image relative depth for a folder of photos: Depth Anything V2 Small
(Apache-2.0; its larger siblings are non-commercial). Run in the trainer's
venv, which has torch with CUDA, by `pipeline surround`:

    python mono_depth.py <photos> <out> <model folder>

Writes <out>/<photo stem>.npy: float32, larger is nearer, nought is sky.
The network sees each photo at 1036 pixels on its long side, and its
mirror image too; the two are averaged.
"""

import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from transformers import AutoModelForDepthEstimation

SIDE = 1036  # a multiple of the network's 14-pixel patches


def main(photos: Path, out: Path, weights: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    model = AutoModelForDepthEstimation.from_pretrained(str(weights)).cuda().eval().half()
    mean = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)
    for path in sorted(photos.glob("*.jpg")):
        bgr = cv2.imread(str(path))
        h, w = bgr.shape[:2]
        scale = SIDE / max(h, w)
        nh, nw = round(h * scale / 14) * 14, round(w * scale / 14) * 14
        rgb = cv2.cvtColor(cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_CUBIC),
                           cv2.COLOR_BGR2RGB)
        x = torch.from_numpy(rgb).cuda().permute(2, 0, 1)[None].float() / 255
        both = []
        with torch.no_grad():
            for mirrored in (False, True):
                seen = torch.flip(x, dims=[3]) if mirrored else x
                depth = model(pixel_values=((seen - mean) / std).half()).predicted_depth.float()
                both.append(torch.flip(depth, dims=[2]) if mirrored else depth)
        depth = torch.stack(both).mean(0)
        depth = torch.nn.functional.interpolate(depth[None], (h, w), mode="bicubic",
                                                align_corners=False)[0, 0]
        np.save(out / (path.stem + ".npy"), depth.cpu().numpy().astype(np.float32))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
