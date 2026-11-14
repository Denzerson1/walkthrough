# flat-02 in Unreal Engine 5

Plan for a photoreal render of flat-02: the flat's interior in UE5, the real
street outside its windows (Google Photorealistic 3D Tiles, streamed), the
sun where it stands at a chosen date and time, a 4K still and a 30 s video.

**State, 2026-11-14: researched and decided; nothing built in Unreal yet.**
Unreal Engine is not installed on this machine (only the Epic launcher), and
four facts are still missing (see "Needed from the owner"). Rule 6 of
`CLAUDE.md` applies: nothing below is reported as working until it has run.

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
- **Still unknown:** which floor (the height of the windows above the
  street), the ground's elevation, the date and time to render.

## Research (14 searches, 2026-11-14)

Claims carry their source. "Unverified" means no primary source was read or
it has not been tried here.

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
  district. **Unverified** until the tiles are loaded at the address; it is
  the first thing slice 1 checks.

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
Lumen to work, Path Tracer (or high-quality Lumen) to render.**

1. `pipeline rebuild` gains an export of the flat **without baked light**:
   one glTF, PBR materials, real-world scale, the windows' glass as separate
   meshes. The web viewer's `flat.glb` is untouched.
2. `unreal/flat02` (UE 5.6 or later; C++-free, plugins: Cesium for Unreal,
   Sun Position Calculator, Movie Render Queue, Python Editor Script
   Plugin). Kept out of the web build: nothing under `apps/` reads it, and
   `unreal/**/{Saved,Intermediate,DerivedDataCache,Binaries}` are ignored.
3. `scripts/unreal/build_scene.py`, run by the editor's Python: imports the
   glTF, places it from `site.json` (origin, bearing, floor height), adds
   the tileset, the sun and sky for the date, time and `Europe/Vienna`, the
   cameras from the web flat's waypoints, and a 30 s sequence.
4. Movie Render Queue: a 4K still and a 30 s 1080p or 4K video, attribution
   burned in.
5. Keys in `.env` (`CESIUM_ION_TOKEN`, or `GOOGLE_MAPS_API_KEY`), read by the
   script at build time and never written into a committed asset.

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

| Risk | Sign | Fallback |
|---|---|---|
| No Google photogrammetry at the address | flat, blurry tiles | The surroundings as OSM blocks at their real floor counts (`site.py`, already used for flat-01; ODbL, may be stored), with Sky Atmosphere. Vienna's own city model (CC BY 4.0, [Stadt Wien](https://www.wien.gv.at/stadtentwicklung/stadtvermessung/geodaten/stadtmodell/)) is untextured building volumes; a textured mesh was not confirmed. |
| 8 GB runs out with the Path Tracer at 4K | crash, or black tiles | Movie Render Queue high-resolution tiling (the frame in tiles), fewer tiles' texture memory (higher screen-space error for the distance), then high-quality Lumen with temporal samples. |
| Holes in tiles under the Path Tracer | gaps in the street | Lumen for the final, or the Path Tracer for the interior only. |
| Tiles not loaded when a frame is taken | low-detail street | Warm-up frames, `CesiumCameraManager`, a slower camera. |

## Build slices (each committed on its own)

1. UE project; Cesium tiles at the address; **check coverage**; screenshot.
2. Interior exported, imported, placed by `site.json`; compare the window
   bearings with the tiles' streets.
3. Sun and sky from date, time, time zone; check against `sky.py`.
4. Materials and glass; exposure against a daylight photo.
5. A 4K still; compared with a real photo from the same window (brightness,
   colour, view).
6. The 30 s video.

## Needed from the owner

1. **Which floor** the flat is on (and a storey height if known).
2. **Date and time** to render (the time zone is Europe/Vienna).
3. **OK to install Unreal Engine 5.6+** through the Epic launcher (tens of
   GB) and the Cesium for Unreal plugin from Fab.
4. **A Cesium ion account and token** (free; the owner creates it), or a
   Google Maps Platform key with the Map Tiles API (needs a billing
   account). The token goes in `.env`.
5. **One daylight photo out of a window**, with its time, for the check.
