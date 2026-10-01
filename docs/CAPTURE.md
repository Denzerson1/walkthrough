# Capture guide

How to film an apartment so the pipeline can reconstruct it. Getting this
right is cheaper than any amount of work downstream: a bad capture cannot be
rescued by better training.

## Equipment

- iPhone 16 Pro
- **Blackmagic Camera** (free, App Store) — the stock Camera app will not let
  you lock exposure, white balance and lens the way this needs
- Optional: an app built on Apple **RoomPlan** that exports USDZ or JSON

## Camera settings

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

## RoomPlan (recommended)

Capture a RoomPlan scan of the same flat, in the same visit. It gives us
walls, doors, windows and furniture bounding boxes directly, which makes
floor polygons, automatic layout and furniture recolouring far more reliable
than deriving them from the splat.

Export USDZ or JSON and keep it alongside the video. The pipeline prefers
RoomPlan data when it is present and falls back to its own estimates when it
is not.

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

```bash
pnpm pipeline -- ingest /path/to/video.mov --project flat-01 \
  --roomplan /path/to/roomplan-export
```

Then tell us the reference distance you measured and what it was (for example
"front door width, 0.82 m"), so the scene can be scaled to metres.

## Checklist before leaving the flat

- [ ] Lens locked to 0.5×
- [ ] Shutter, ISO, white balance and focus all locked
- [ ] Stabilisation off, HDR off
- [ ] Every light on
- [ ] Reference distance measured and written down
- [ ] Every room filmed, including a 1 m circle at three heights per waypoint
- [ ] Walked through every doorway
- [ ] RoomPlan scan captured
- [ ] Watched the footage back once for blur or a lens switch
