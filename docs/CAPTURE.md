# Capture guide

How to film an apartment so the pipeline can reconstruct it. Getting this
right is cheaper than any amount of work downstream: a bad capture cannot be
rescued by better training.

## Equipment

- iPhone 16 Pro (any iPhone with LiDAR)
- **Stray Scanner** (free) — the capture
- **Polycam** (free tier) — Floorplan mode, for walls and doors
- Only for video-only capture: **Blackmagic Camera** (free)

## Recommended: Stray Scanner + Polycam Floorplan

Two free apps, one visit, nothing moved in between.

1. **[Stray Scanner](https://apps.apple.com/us/app/stray-scanner/id1557051662)**
   (free, MIT-licensed source) records video **plus the phone's own camera
   positions (ARKit) and LiDAR depth for every frame**. The pipeline uses
   those positions directly instead of guessing them from the footage, and
   the depth keeps plain walls solid. The scene comes out in real metres.
2. **Polycam → Floorplan mode** gives walls, doors and furniture boxes;
   export glTF (`.glb`).

**Filming with Stray Scanner**

- Bright, even light: every lamp on, daylight if possible. The app exposes
  automatically, so a bright room is what keeps frames sharp.
- Hold the phone in landscape, steadily, and move **slowly** — slower than
  feels necessary, especially when turning.
- Start in the middle: a slow circle facing outward at eye, chest and knee
  height. Then walk along the walls about 1–2 m from them, and finish by
  crossing the room. Point at the floor and ceiling now and then.
- 1.5–3 minutes per room. Stop and start a new recording per room.

**Getting it to the PC:** Files app → On My iPhone → Stray Scanner → the
recording's folder (it holds `rgb.mp4`, `odometry.csv`, `depth/`,
`confidence/`). Copy the whole folder.

```bash
pnpm run pipeline run-all "C:\captures\stray\8a3f1c2e" --project flat-01 --roomplan "C:\captures
oom.glb"
```

The rest of this guide covers the older **video-only** capture (Blackmagic
Camera), still supported: the pipeline then recovers camera positions with
COLMAP, which struggles on plain walls and gives no real scale.

## Video-only capture: camera settings

Set these once, then do not touch them during the shoot. Anything that
changes mid-capture — exposure, white balance, focus, lens — becomes an
inconsistency the reconstruction has to average away.

| Setting | Value | Why |
|---|---|---|
| Lens | Ultra wide **0.5× (13 mm, 48 MP)**, selected explicitly | Wide coverage, and an explicit choice stops the phone switching lenses mid-walk |
| Resolution | 4K | Detail survives the downsample to ~1600 px for training |
| Frame rate | 60 fps | More frames to choose from after blur rejection |
| Codec | HEVC, high bitrate | Compression artefacts become reconstruction noise |
| Shutter | **1/500 s** (at least 1/250 s in dark rooms — walk slower) | Motion blur is the single biggest cause of failed reconstructions |
| ISO | As low as the shutter allows, then **locked** | Noise is reconstructed as geometry |
| White balance | Manual, matched to the lights (~4000–5000 K), then **locked** | Auto WB shifts colour between rooms and the splat inherits the inconsistency |
| Focus | Autofocus **off**, set once at about 1.5 m | Focus hunting ruins frames |
| Stabilisation | **Off** | Stabilisation warps the image, so the camera model no longer matches reality |
| Lens correction | Leave iOS's default on | The pipeline assumes the ultra-wide is mostly rectified, as iOS records it (camera model `OPENCV`). If frames look fisheye, rerun `poses --camera-model OPENCV_FISHEYE` |
| Colour | **Rec. 709 SDR** — no HDR, no Apple Log | The pipeline expects standard-range sRGB-ish input |

## Preparing the flat

- Switch **all** the lights on and leave them alone for the whole shoot.
- Doors fully open or fully closed, never halfway.
- No people or pets moving. Anything that moves becomes a smear.
- Tidy away anything you would not want a buyer to see. Privacy masking in the
  editor is a safety net, not a substitute.
- Open blinds consistently. Avoid shooting into direct sun.
- **Measure one reference distance** and write it down — a door width is
  ideal. Without it the scene has no real-world scale.

## Filming

- Walk **slowly**. Slower than feels necessary.
- **Never rotate on the spot.** Pure rotation gives the reconstruction no
  parallax, so it cannot work out depth. Always translate while you turn.
- At every room's viewpoint, walk a **circle of about 1 m facing outward**, at
  three heights: eye, chest, knee. This is what makes the 360° look-around
  work from that waypoint.
- **Walk through the doorways**, slowly, so the rooms connect into a single
  model. If you teleport between rooms, you get two unrelated scenes.
- Keep at least **60% overlap** between consecutive frames. At 60 fps and
  walking pace this is automatic — but not if you hurry.
- Cover the floor properly: it has to be reconstructed well for M3's floor
  detection and replacement to work.
- Shoot each room for roughly 60–90 seconds.

## RoomPlan scan (strongly recommended)

Capture a RoomPlan (LiDAR) scan of the same room, in the same visit, with the
room exactly as filmed. It is what makes the result right rather than
approximately right:

- **Real-world scale.** The video alone has no idea of metres. `align` fits
  the splat's walls onto the scan's walls and takes scale from that — within
  millimetres. Without a scan, scale is guessed from a typical phone height
  and can be 10–20% off.
- **Walls, doors and windows**, so the viewer knows where you can walk and
  the layout solver knows where furniture can go.
- **Furniture boxes** for recolouring existing pieces.

Tested with **Polycam's Floorplan mode, glTF export**. Any other RoomPlan app
works if it exports RoomPlan JSON or USDZ. A plain LiDAR mesh (Polycam "3D
Space", OBJ/PLY) is not the same thing: it has no labelled walls or doors and
the pipeline cannot use it.

Scan tips: walk the room slowly, point at every wall, door and window until
the app outlines it, and finish the scan before moving anything.

A rectangular room with doors and windows placed symmetrically can fit the
scan either way round; `align` warns when that happens.

## Consent and privacy

The first two apartments are the user's own and a friend's, so this is
straightforward. Before capturing **anyone else's** home:

- [ ] Get the owner's written permission to capture and publish a 3D scan
- [ ] If an estate agent arranges access, confirm the agent has the owner's
      permission — the agent's own consent is not enough
- [ ] Tell them the scan will be published on a public link
- [ ] Agree how to handle deletion if they later ask
- [ ] Remove or mask personal photographs, documents, post, and anything with
      a name or address on it
- [ ] Mask house numbers and anything identifying visible through windows

A published scan of someone's home, including their belongings, is personal
data under GDPR. Treat it that way.

## Handing the capture over

One command, from the repo in a Windows terminal (Windows paths are fine).
The capture is either a Stray Scanner folder or a video file:

```bash
pnpm run pipeline run-all "C:\captures\stray\8a3f1c2e" --project flat-01 --roomplan "C:\captures
oom.glb"
pnpm run pipeline run-all "C:\captures\living.mov" --project flat-01 --roomplan "C:\captures
oom.glb"
```

`ingest` checks the capture (a video against this guide's settings) and
parses the scan before anything slow starts, so a wrong export fails in
seconds, not after an hour of training.

With a RoomPlan scan, the measured reference distance is a cross-check only.
Without one, write it down anyway: marking it in the editor is the planned way
to fix scale, and is not built yet.

## Checklist before leaving the flat

- [ ] Lens locked to 0.5×
- [ ] Shutter, ISO, white balance and focus all locked
- [ ] Stabilisation off, HDR off
- [ ] Every light on
- [ ] Reference distance measured and written down
- [ ] Every room filmed, including a 1 m circle at three heights per waypoint
- [ ] Walked through every doorway
- [ ] RoomPlan scan captured and exported (JSON if the app offers it)
- [ ] Watched the footage back once for blur or a lens switch
