# Roadmap

What is deliberately out of scope for the MVP, and what has to be bought or
decided before launch.

## Blocked on things only you can provide

| Item | Blocks | Notes |
|---|---|---|
| A working NVIDIA GPU | M1 end to end | The machine this was built on reports only AMD integrated graphics. The 2080 Super is the plan (docs/SPEC.md §2) but was never visible to this session. |
| WSL2 + CUDA toolchain | M1 | Only the `docker-desktop` WSL distro exists. No Ubuntu, no ffmpeg, no COLMAP. Setup steps in `docs/PIPELINE.md`. |
| A real iPhone capture | M1 validation | Everything so far runs on a synthetic scene. |
| `ANTHROPIC_API_KEY` | M5 eval pass rate | 32 eval cases are written and the harness runs; the rate is unmeasured. |
| Cloudflare R2 credentials | M10 | Storage adapter has the interface; the S3 backend raises rather than pretending. |
| A domain | M10 | Share links currently use `window.location.origin`. |
| The brand name | M10 | Everything says `walkthrough`. |

## Purchases to consider

### Furniture models

The catalog has 23 items across 4 stocked styles, but **no mesh has been
downloaded**. Every entry is `assetStatus: "pending"` and the viewer draws a
correctly sized placeholder box.

CC0 sources (Khronos glTF Sample Assets, Poly Haven) cover generic shapes
well but are thin on period-specific furniture. Expect gaps in:

| Style | Likely gap | Rough cost |
|---|---|---|
| Traditional | Chesterfield sofa, Windsor chair, period bookcase | €150–400 for a usable set |
| Mid-century | Licensed-lookalike lounge chairs, teak sideboards | €200–500 |
| Contemporary | Boucle seating, large-format soft shapes | €150–350 |

Marketplaces worth pricing: TurboSquid, CGTrader, Sketchfab Store. Check each
model's licence individually — "royalty free" is not the same as "commercial
use permitted", and some forbid redistribution inside a web app, which is
exactly what we would be doing.

Where a style has a visible gap, a `DEMO-ONLY` asset may be used for the
investor demo under the pragmatic posture, but only if it is listed in
`docs/LICENSES.md` with a must-replace note and an entry here.

### Floor textures

11 of the 16 floors point at ambientCG (CC0) but the maps are not
downloaded. That is a bandwidth decision, not a licence one — the brief
requires asking before downloading anything large. Roughly 20–60 MB per
material at 2K.

## Explicitly out of scope for the MVP

From the brief: self-service upload, user accounts, payments, and **swapping
or removing existing furniture**.

### Design note: removing existing furniture

The brief asks for this to be written down even though it is out of scope.

To remove a piece of furniture that is physically in the capture:

1. **Identify the splats.** Take the item's oriented box, from RoomPlan or
   drawn in the editor, and select every Gaussian whose centre falls inside
   it. The tagging work for this already exists for recolouring (M8).
2. **Delete them.** Straightforward, and leaves a hole: the wall and floor
   behind the sofa were never observed by the camera, so there is nothing
   there.
3. **Inpaint what was behind.** This is the hard part, and the reason it is
   out of scope. Options, roughly in order of effort:
   - *Plane extension.* If the hole backs onto a flat wall or the floor,
     extend the surrounding plane across it and sample nearby texture.
     Cheap, works for a sofa against a plain wall, fails at corners, skirting
     boards, radiators and anything patterned.
   - *Multi-view fill.* Other frames may have seen part of the occluded
     region from a different angle. Re-project what is available before
     inventing anything. Best quality where it applies, but coverage is
     usually partial.
   - *Generative 2D inpainting, then re-fit.* Render the hole from several
     viewpoints, inpaint each in 2D, and fit new Gaussians to the results.
     Highest quality, but the views disagree with each other and reconciling
     them is an open problem. Also a licence question for whichever model.
4. **Place a catalog item using the original box as the layout hint.** The
   removed item's box tells us exactly where a replacement fits and which way
   it faces, which is better information than the auto-layout solver has.

The honest sequencing: do not attempt this until floor replacement (M3) is
convincing on real captures. It is the same class of problem — replacing
observed geometry with plausible invented geometry — and the floor is the
easy case because it is planar and its lighting can be recovered.

## Known limitations to fix before launch

- `scene.spz` is never produced because the `spz` CLI is not installed, so
  scenes ship as PLY — roughly 3–5× larger. Directly threatens the brief's
  60 MB per-apartment target.
- Metric scale is not settable: `align --scale-ref` warns and does nothing
  because marking the two reference points needs an editor tool that is not
  built.
- The editor cannot draw floor polygons or privacy boxes on the splat yet. It
  can edit everything else in the manifest.
- Furniture renders as placeholder boxes until GLBs are sourced.
- No environment map from the splat, and no contact shadows, so placed
  furniture does not sit in the scene as convincingly as it should (M6).
- The assistant's `apply_style` and `auto_layout` currently apply the style's
  floor and add its picks at the room centre. The real solver exists and is
  tested in `walkthrough_pipeline.layout`, but is not wired to the API yet.
- iOS device-orientation look-around (with the permission prompt) is not
  implemented; drag and pinch are.
