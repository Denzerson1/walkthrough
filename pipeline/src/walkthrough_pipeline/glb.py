"""
A minimal glTF 2.0 binary writer: one mesh, one unlit textured material.

For geometry the pipeline makes outside Blender (the view out of the
windows), where a whole Blender run for one textured mesh is not worth it.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

_FLOAT, _UINT32 = 5126, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


Mesh = tuple[np.ndarray, np.ndarray, np.ndarray, bytes]


def write_textured_meshes(path: Path, meshes: list[Mesh], name: str) -> None:
    """
    Meshes, each: positions (n, 3) in scene metres, texture coordinates
    (n, 2) with v running down the image (glTF's convention), triangle
    indices (m, 3), and the JPEG its material shows unlit. Nodes and
    materials are called `name`.
    """
    chunks: list[bytes] = []
    views: list[dict] = []

    def add(data: bytes, target: int | None = None) -> int:
        offset = sum(len(c) for c in chunks)
        chunks.append(data + b"\0" * (-len(data) % 4))
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        views.append(view)
        return len(views) - 1

    accessors: list[dict] = []
    doc_meshes, materials, images = [], [], []
    for k, (positions, uvs, indices, jpeg) in enumerate(meshes):
        positions = np.ascontiguousarray(positions, np.float32)
        uvs = np.ascontiguousarray(uvs, np.float32)
        indices = np.ascontiguousarray(indices, np.uint32).ravel()
        accessors += [
            {"bufferView": add(positions.tobytes(), _ARRAY_BUFFER), "componentType": _FLOAT,
             "count": len(positions), "type": "VEC3", "min": positions.min(axis=0).tolist(),
             "max": positions.max(axis=0).tolist()},
            {"bufferView": add(uvs.tobytes(), _ARRAY_BUFFER), "componentType": _FLOAT,
             "count": len(uvs), "type": "VEC2"},
            {"bufferView": add(indices.tobytes(), _ELEMENT_ARRAY_BUFFER),
             "componentType": _UINT32, "count": len(indices), "type": "SCALAR"},
        ]
        doc_meshes.append({"name": name, "primitives": [{
            "attributes": {"POSITION": 3 * k, "TEXCOORD_0": 3 * k + 1}, "indices": 3 * k + 2,
            "material": k}]})
        materials.append({
            "name": name,
            "pbrMetallicRoughness": {"baseColorTexture": {"index": k},
                                     "metallicFactor": 0.0, "roughnessFactor": 1.0},
            "extensions": {"KHR_materials_unlit": {}},
            "doubleSided": True,
        })
        images.append({"bufferView": add(jpeg), "mimeType": "image/jpeg"})
    doc = {
        "asset": {"version": "2.0", "generator": "walkthrough pipeline"},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": list(range(len(meshes)))}],
        "nodes": [{"mesh": k, "name": name} for k in range(len(meshes))],
        "meshes": doc_meshes,
        "materials": materials,
        "textures": [{"source": k, "sampler": 0} for k in range(len(meshes))],
        # Clamped: a photo is not a repeating texture.
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": images,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": sum(len(c) for c in chunks)}],
    }
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    binary = b"".join(chunks)
    total = 12 + 8 + len(text) + 8 + len(binary)
    with open(path, "wb") as out:
        out.write(struct.pack("<4sII", b"glTF", 2, total))
        out.write(struct.pack("<I4s", len(text), b"JSON") + text)
        out.write(struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def read_json(path: Path) -> dict:
    """The JSON chunk of a .glb, for tests and checks."""
    data = Path(path).read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20 : 20 + length])
