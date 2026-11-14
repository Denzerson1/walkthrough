# flat-02 in Unreal Engine 5

Plan for a photoreal render of flat-02: the flat's interior in UE5, the real
street outside its windows (Google Photorealistic 3D Tiles, streamed), the
sun where it stands at a chosen date and time, a 4K still and a 30 s video.

**State, 2026-11-14: built and rendered.** `unreal/flat02` (UE 5.8.4, Cesium
for Unreal 2.31.0) is built from nothing by `scripts/unreal`, with Google's
tiles outside and the sun for 15 October 2026, 15:45. A 4K path-traced still
and a 30 s 1080p walk came out of Movie Render Queue. What was measured, and
what is still assumed or rough, is under "Built and measured" below; the
research and the decision that led here follow it unchanged except where
marked.

The street address, coordinates and OSM building id are personal data and
stay out of git: they are in `data/projects/flat-02/input/site.json`.

## What flat-02 is (read from the repo, not assumed)

- **Not a capture.** There is no video, LiDAR or Gaussian splat of flat-02
  under `data/`. It is a Blender rebuild (`pipeline rebuild`,
  `pipeline/blender/build_flat.py`) from a Polycam Floorplan scan
  (`input/roomplan/scan.glb`), the architect's plan (`dressing.json` `plan`)
  and 89 photos. The output is `scene/flat.glb` with baked light maps.
  "Interior from the real capture" therefore means: the plan's walls, doors
  and windows and the rebuilt furniture, re-lit in Unreal. A splat of this
  flat needs a new Stray Scanner visit (`docs/CAPTURE.md`).
- **All 89 photos were taken at night** (EXIF 21:58–22:06, 3 Nov 2026) and
  carry no GPS. There is no daylight photo out of a window to compare a
  render with; one has to be taken (note the time).
- **Location, measured.** The building was found in OpenStreetMap by its
  address; the plan's outline was laid on its footprint
  (`work/site/site_plan.png`). It fits the building's west end: the 5.8 m
  balcony recess and the 8.5 m north wall both match. From that:
  the balcony side (scene −z) faces **191.5°**, the end wall's window (+x)
  **281.5°**, the north windows (+z) **11.5°**, each ±2° (the footprint was
  traced from aerial imagery). `dressing.json` still holds the old
  assumption (south, 180°, a city-centre coordinate); it is not changed
  until the next bake is wanted.
- **From the owner (2026-11-14):** the first floor (one above the ground
  floor). Its height above the street is not measured: it is set in the
  editor by laying the balcony on the building in the tiles.
- **Date and time, chosen for the owner:** 15 October 2026, 15:45,
  Europe/Vienna (CEST). `flat_geometry.sun_position` puts the sun at 229.8°,
  20.3° high: 38° off the balcony glazing's normal and low enough to pass
  under the 3 m overhang onto the glass's lower 1.1 m, and 52° off the west
  window's, a patch 5.8 m along the floor. At the equinox's noon the
  overhang shades the south glass entirely.
- **Still unknown:** the ground's elevation (read from the tiles).

## Built and measured (2026-11-14)

```bash
python scripts/unreal/prepare_flat.py     # the model and scene.json for Unreal
python scripts/unreal/run.py build        # the level, from an empty one (about 1-3 min)
python scripts/unreal/run.py render       # still + walk (about 11 min on the 2080 Super)
```

Needs `CESIUM_ION_TOKEN` in `.env`, Unreal 5.6+ with Cesium for Unreal, and
ffmpeg in WSL for the video. Outputs, all git-ignored, are in
`data/projects/flat-02/unreal/`: `out/flat02_still.png` (3840 x 2160),
`out/flat02_walk.mp4` (1920 x 1080, 900 frames, 30 fps), `report.json`.

**How it is put together**

- `prepare_flat.py` takes the web flat's `scene/flat.glb` and drops what
  only the browser needs: the painted backdrop outside the windows and the
  baked light. The sliding door stands open. It writes the placement facts
  (`scene.json`) from `site.json`. Tested (`test_unreal_prepare.py`): the
  bearings, that the plan is turned and not mirrored, the outline.
- `in_editor.py` (inside the editor): Cesium's georeference at the flat,
  the Google tileset through Cesium ion, `CesiumSunSky` for the date and
  time, the model through Interchange, placed by a fit over six furniture
  centres, glass and sheers that let light through, a warm light under each
  of the 11 ceiling lamps, exposure and white balance.
- `render.py`: the still with the Path Tracer (256 samples, denoised), the
  walk with Lumen on hardware ray tracing (8 temporal samples), exposure
  fixed for the still and adapting between EV 7 and 10.8 on the walk.
- `run.py` starts the editor, then stamps Google's attribution on the still
  and the video and encodes the video.

**Measured**

| What | Result |
|---|---|
| Google's tiles at the address | 3D houses, on their OpenStreetMap outlines to a metre or two (`heightmap.png`, 121 x 121 samples) |
| **The flat's own building** | **not in the tiles**: an empty plot with trees. Google's imagery is older than the building. |
| Ground under the flat | 199.91 m above the ellipsoid (median of 81 samples, 199.6-206.3 with the trees) |
| Floor of the flat | 203.07 m: ground + 0.30 m plinth + 2.86 m storey. **The plinth and the slab are assumed**, there is no building in the tiles to lay the balcony on. |
| Placement | the six pins within 0.0 cm of where the bearings put them; a pure rotation (determinant 1.0) |
| Sun | Cesium: 229.55 deg, 20.02 deg high. NOAA (`flat_geometry.sun_position`): 229.8 deg, 20.3 deg. 0.33 deg apart, two independent calculations. |
| Still | 171 s, no out-of-memory on 8 GB |
| Walk | 400 s for 900 frames |
| Still against the owner's photos | median luminance 0.27 (photos 0.24-0.32), red/blue 1.30 (photos 1.21-1.30). The photos are night shots under lamps, so this ties down the overall tone only, not daylight. |

**What is not right or not checked**

- **No daylight photo exists**, so the view out of a window is not checked
  against one. It is the street as Google flew it: close to the glass the
  photogrammetry is coarse (the neighbour's house through the west window is
  a soft shape, trees are lumps), and anything built since is missing, as
  the flat's own building is.
- Trees and the neighbour in the tiles shade the west window at this hour;
  the sun patch on the floor is shorter than the open-sky figure in
  `site.json`. Whether those trees still stand is not known.
- The floor height rests on two assumed figures (above); +-0.5 m.
- The interior is the Blender rebuild, re-lit: its materials are the web
  flat's colours and textures with roughness only, no normal maps. It reads
  as a good render, not as a photograph.
- The attribution is the text Google sends ("Google"), not its logo.
  Whether a published marketing video is covered by the Map Tiles API
  policies is still unverified; read them before publishing.
- The level saves the Cesium token inside `Flat02.umap` (git-ignored).
  Cesium keeps an HTTP cache next to the project, following the tiles' own
  cache headers; it is ignored and must not be shared.
- Only the living room is in the still; the walk covers the hall and living
  room. Other rooms are in the level but were not looked at.

**Found on the way (each cost a run)**

- `-ExecutePythonScript` quits the editor when the script returns; the build
  runs over many ticks, so `run.py` uses `-ExecCmds="py ..."`.
- The editor stops ticking the world when its window is not in front; no
  tile ever loads. `run.py` switches that off for its run, and the ground is
  measured in a play session, which ticks regardless.
- A sky spawned from a script keeps Cesium's start position (Colorado): the
  sun was 119 deg out until its globe anchor was moved to the flat.
- Under shader model 5 there is no hardware ray tracing: the Path Tracer is
  refused and Lumen's bounce light is weak. The project targets SM6.
- Cesium waits for its tiles only for sequences that are in the level when
  play begins; Movie Render Queue starts its own. `render.py` puts the
  tilesets in movie-capture mode itself.
- An import pumps the editor's loop, which re-entered the step runner and
  imported the flat twice.
- `scripts/unreal/dev.sh` with `run.py dev` tries a snippet in the open
  editor; it is how these were found.

## Research (14 searches, 2026-11-14)

Claims carry their source. "Unverified" means no primary source was read or
it has not been tried here.

Written before anything ran. Where "Built and measured" above says
otherwise (coverage, the sun, 8 GB, the tiles in a render), that is what
was found; the items it does not mention are as unverified as they were.

### a) Cesium for Unreal + Google Photorealistic 3D Tiles
- Setup is: connect to Cesium ion in the Cesium panel, set a project token,
  "Quick Add" Google Photorealistic 3D Tiles, then place the
  `CesiumGeoreference` origin
  ([Cesium tutorial](https://cesium.com/learn/unreal/unreal-photorealistic-3d-tiles/)).
  Google's own route needs a Cloud project with billing and the Map Tiles
  API enabled ([Google](https://developers.google.com/maps/documentation/tile/3d-tiles)).
- Versions: Cesium for Unreal v2.29.0 (Aug 2026) requires **UE 5.6 or
  later**; v2.28.0 added UE 5.8; v2.30.0 is current (Oct 2026)
  ([Aug](https://cesium.com/blog/2026/08/04/cesium-releases-in-august-2026/),
  [Jul](https://cesium.com/blog/2026/07/01/cesium-releases-in-july-2026/),
  [Oct](https://cesium.com/blog/2026/10/02/cesium-releases-in-october-2026/)).
- Terms: pre-fetching, caching, storage and offline use of tile content are
  prohibited except as the service terms allow; attribution returned with
  the tiles must be shown with the rendered content; own 3D objects may be
  overlaid if they are not derived from the tiles
  ([Map Tiles API policies](https://developers.google.com/maps/documentation/tile/policies)).
  So: **stream only, never export the tiles**, and keep Google's attribution
  in the still and the video. Whether a rendered marketing video is covered
  is **unverified**; read the policies page before publishing one.
- Cost on Google's own key: 1,000 root-tile requests a month free, then
  $6 per 1,000; one editor session is roughly one root request
  ([usage and billing](https://developers.google.com/maps/documentation/tile/usage-and-billing)).
  Through Cesium ion's free tier no Google billing account is needed
  (**unverified**: ion's current quota and its terms for this use).
- **Coverage is the main risk.** Google lists about 2,500 cities in 49
  countries ([Google](https://mapsplatform.google.com/resources/blog/create-immersive-3d-map-experiences-photorealistic-3d-tiles/));
  no source confirmed photogrammetry for this outer part of Vienna's 22nd
  district. Checked through Cesium ion with `scripts/check_3d_tiles.py`
  (the tile tree only, nothing stored): the finest tile at the flat is 51 m
  across with a 2.0 m geometric error, the same as Vienna's centre, where a
  place without coverage stops at 2.4 km and 128 m. So the detailed data
  reaches the address; that the houses stand in 3D there is **not yet
  seen** and is the first thing slice 1 looks at.

### b) Placement
- `CesiumGeoreference` origin at the flat's latitude, longitude and height;
  the interior as one actor (or sub-level) rotated about the vertical by the
  measured bearing; the floor's height above the street added in Z
  ([Cesium tutorial](https://cesium.com/learn/unreal/unreal-photorealistic-3d-tiles/)).
  Scene axes are Y-up metres with −z the balcony side (`site.json`
  `bearings`); Unreal is Z-up centimetres, so the import converts once.
- Height: the ground under the building is read from the tiles in the
  editor, not typed in. The floor's height needs the owner's floor number
  and, ideally, one measured storey height.

### c) Sun and sky
- Epic's Sun Position Calculator plugin (the `SunSky` actor: latitude,
  longitude, time zone, date, solar time) with Sky Atmosphere and volumetric
  clouds; Cesium ships a georeferenced variant, `CesiumSunSky`.
  **Unverified**: the search for the current docs failed; property names are
  to be read in the editor. The repo already computes the sun's bearing and
  height for a date and place (`pipeline/src/walkthrough_pipeline/sky.py`),
  which gives an independent check of the actor's sun direction.

### d) Interior
- **glTF through Interchange** is the engine's maintained importer and the
  one Epic develops; Datasmith's glTF importer was deprecated and removed
  ([Epic docs](https://dev.epicgames.com/documentation/unreal-engine/importing-gltf-files-into-unreal-engine),
  [forum](https://forums.unrealengine.com/t/datasmith-gltf-importer-plugin-missing/1313281)).
  Material instances come in as defaults and are re-assigned by a script
  ([forum](https://forums.unrealengine.com/t/ue5-6-interchange-gltf-how-interchangegltfpipeline-is-supposed-to-replace-customize-imported-materials/2597367)).
- **Gaussian splats in UE**: XScene-UEPlugin (Apache-2.0) lists UE 5.1–5.5,
  last updated July 2025, Niagara-based; Lumen interaction is untested by
  its authors ([repo](https://github.com/xverse-engine/XScene-UEPlugin),
  [guide](https://www.3dgsviewers.com/learn/guide/xscene-ue-plugin)). Cesium
  needs 5.6+, so the two do not meet today.
- **Lumen vs Path Tracer**: the Path Tracer needs DXR hardware (RTX) and, per
  Epic's Twinmotion page, at least 8 GB VRAM, 12 GB recommended
  ([Epic](https://dev.epicgames.com/documentation/en-us/twinmotion/path-tracer-requirements-for-twinmotion),
  [Epic](https://dev.epicgames.com/documentation/en-us/unreal-engine/ray-tracing-and-path-tracing-features-in-unreal-engine)).
  Users report holes in Cesium tiles under the Path Tracer and tiles not
  loaded in time in Movie Render Queue; warm-up frames and a
  `CesiumCameraManager` in the sequence are the usual fixes
  ([holes](https://community.cesium.com/t/holes-when-rendering-with-path-tracing/29456),
  [MRQ](https://community.cesium.com/t/ue-5-1-rendering-with-movie-render-queue-tiles-not-loading-in-time/24954)).
- Exposure, white balance and lens: matched to a daylight photo once one
  exists (the photos' lens is 24 mm equivalent, from EXIF). Glass: thin
  translucent panes and manual exposure, so the street is not blown out.
  **Unverified** until rendered.

### e) Blender MCP
- `ahujasid/blender-mcp` drives a running Blender from Claude over a socket
  add-on: objects, materials, viewport screenshots, Poly Haven and Sketchfab
  assets ([repo](https://github.com/ahujasid/blender-mcp)). The flat is
  already built by a headless, repeatable script; MCP edits are interactive
  and would not survive a rebuild. Use it only to look at a piece while
  fixing `build_flat.py`, never as the source of truth. Blender exists here
  only inside WSL (headless); the MCP route needs Blender on Windows.

### f) Seedance and other video AI
- Seedance 2.0 follows camera prompts and a start image, clips of at most
  15 s, with identity and layout drifting after a few seconds
  ([Artlist](https://help.artlist.io/hc/en-us/articles/37101319322909-Seedance-2-0-Family),
  [Kapwing](https://www.kapwing.com/resources/how-to-use-seedance-2-0-a-guide-for-ai-video-creators/)).
  Nothing found shows geometry-true architecture. Optional polish of
  marketing clips only; not in this pipeline, and it would send images of
  the flat to a third party.

### g) Hardware: RTX 2080 Super, 8 GB, 32 GB RAM
- Hardware Lumen and the Path Tracer both run on RTX 20-series
  ([Epic](https://dev.epicgames.com/documentation/en-us/unreal-engine/ray-tracing-and-path-tracing-features-in-unreal-engine)).
  8 GB is the Path Tracer's floor; third-party guides put Lumen interiors at
  12–16 GB ([iRender](https://irendering.net/unreal-engine-real-time-rendering-needs-more-gpu-than-you-have/),
  not Epic). Whether Cesium tiles + the flat + a 4K frame fit in 8 GB is
  **unverified**; see the fallback.
- Native Windows Unreal; nothing of this runs in WSL2. Free disk: 277 GB on
  C:. The engine's install size was not found in a primary source.

## Decision

**Blender rebuild → glTF → UE 5.6+ on Windows → Cesium tiles outside →
Lumen to work and for the walk, the Path Tracer for the still.**

As built it differs from the first plan in three ways: the web flat's
`flat.glb` is reused (stripped by `prepare_flat.py`) instead of a second
export from Blender; the sun is Cesium's `CesiumSunSky`, checked against
the repo's own calculation; and nothing is clipped out of the tiles,
because the building is not in them.

Keys stay in `.env` (`CESIUM_ION_TOKEN`), handed to the editor in its
environment. `unreal/` is outside the web build: nothing under `apps/`
reads it, and what the editor generates is git-ignored.

### Rejected

- **A Gaussian splat of the flat in Unreal** (XScene plugin). There is no
  splat of flat-02; the plugin stops at UE 5.5 while Cesium needs 5.6; and a
  splat keeps the light it was filmed in, so a sun for a chosen date cannot
  relight it.
- **Staying in the browser and baking the Google tiles into a panorama or
  backdrop for the web viewer.** That stores tile content offline, which the
  Map Tiles API policies prohibit. (The browser flat can instead get the
  real surroundings as OpenStreetMap blocks, see the fallback.)
- Also not chosen: video AI for the core result (not geometry-true), and
  Blender Cycles with imported Google tiles (the import saves the tiles).

### Cost

Unreal Engine and Cesium for Unreal are free for this use. Cesium ion's free
tier or Google's 1,000 free root requests a month should cover the work;
beyond that Google charges $6 per 1,000. **Nothing is bought or billed
without asking first.**

### VRAM risk and fallback

Outcome: none of these was needed. The Path Tracer rendered 4K at 256
samples within 8 GB, the tiles had no holes, and they loaded in time once
in movie-capture mode.

| Risk | Sign | Fallback |
|---|---|---|
| No Google photogrammetry at the address | flat, blurry tiles | The surroundings as OSM blocks at their real floor counts (`site.py`, already used for flat-01; ODbL, may be stored), with Sky Atmosphere. Vienna's own city model (CC BY 4.0, [Stadt Wien](https://www.wien.gv.at/stadtentwicklung/stadtvermessung/geodaten/stadtmodell/)) is untextured building volumes; a textured mesh was not confirmed. |
| 8 GB runs out with the Path Tracer at 4K | crash, or black tiles | Movie Render Queue high-resolution tiling (the frame in tiles), fewer tiles' texture memory (higher screen-space error for the distance), then high-quality Lumen with temporal samples. |
| Holes in tiles under the Path Tracer | gaps in the street | Lumen for the final, or the Path Tracer for the interior only. |
| Tiles not loaded when a frame is taken | low-detail street | Warm-up frames, `CesiumCameraManager`, a slower camera. |

## Build slices

All six ran: project and tiles, the interior placed, the sun, materials and
glass, the 4K still, the 30 s video. See "Built and measured".

## Open, for the owner

1. A daylight photo out of a window, with its time, would let the view and
   the light be checked; without it they are checked against calculation.
2. The floor's real height above the street (or the storey height) would
   replace the two assumed figures.
3. Google's policies before any render is published.
