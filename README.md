# AutoNav-0 Lite — headless Mars map service, 2D/3D client and ROS 2 rover twin

An offline-first, open-source take on [NASA Mars Trek](https://trek.nasa.gov/mars/) for the NASA Space
Apps *Interplanetary Survival Guide: Martian Map* challenge. A small C++17 daemon (`autonav0d`) serves
cached NASA map layers, MOLA elevation and TES thermal inertia, **precomputed terrain-class layers**
(relief landforms planet-wide + DoMars16k surface landforms for 41 regions), risk-aware route comparison
and Mars solar geometry; a lightweight web client (Leaflet 2D + optional three.js 3D, served by the daemon)
gives a Trek-style layered view. An optional, isolated **ROS 2 rover container** replays real Perseverance
Navcam stereo as if live, runs open-source VSLAM (RTAB-Map) and learned stereo/features, and streams the
rover pose back to the map (a Rerun 3D debug viewer is available as a dev-only extra). See `Agenda.md` and
`Research.md` for the roadmap.

```
 ┌──────────── autonav0d (C++17, headless, ~1 MB binary) ─────────────┐
 │ TileCache   offline WMTS cache (+ fill from NASA Trek when online)   │
 │ DemStore    MOLA MEGDR grids in RAM (global 4/16 ppd + 463 m patches)│
 │ Terrain     relief landforms (TPI) + DoMars16k class grids (once)   │
 │ Planner     A* profiles (safe / wheel saver / EVA / shortest),      │
 │             route comparison + alternatives, wear & drift telemetry │
 │ Renderer    hillshade + shadows, slope, contours, class maps (PNG)   │
 │ Mars24      Allison & McEwen (2000) Mars time & sun position         │
 └── HTTP on 127.0.0.1:8080  or  Unix domain socket (--unix PATH) ──────┘
        ▲                       ▲                              ▲ POST /api/rover/state
 web/ 2D (Leaflet)      any IPC client (curl, Qt…)     ┌───────── rover container (ROS 2 Jazzy) ─────────┐
 + 3D (three.js)                                        │ log_player  Navcam stereo replay + drifting odom │
                                                        │ RTAB-Map    stereo VSLAM, loop closure, clouds  │
                                                        │ stereo_depth / visual_odometry (GPU or CPU)     │
                                                        │ autonav_bridge (HTTP)   rerun_bridge (dev only)─┐│
                                                        └─────────────────────────────────────────────────┼┘
                                                     [dev] Rerun server + web viewer (:9090) ◄────────────┘
```

## Quick start (Ubuntu)

```bash
sudo apt install build-essential cmake libcurl4-openssl-dev python3   # libcurl optional
python3 scripts/fetch_data.py          # small offline pack (~40-60 MB), only once
cmake -S . -B build && cmake --build build -j
./build/autonav_tests                  # unit tests (Mars24 reference values, DEM, A*)
./build/autonav0d                      # then open http://127.0.0.1:8080
```

or simply `scripts/run.sh` (build + fetch if needed + run).

Daemon options: `--host`, `--port`, `--unix PATH` (Unix-socket IPC), `--data DIR`, `--web DIR`,
`--upstream URL`, `--offline` (never touch the network), `--rerun-url URL` (Rerun viewer link for the UI). Online/offline can also be toggled at runtime
(the 📡 *Uplink* button, or `POST /api/mode?offline=1`). A hardened systemd unit is in
`systemd/autonav0d.service`.

### Offline data pack (`scripts/fetch_data.py`)

| Item | Size | Source |
|---|---|---|
| Trek layer catalog (165 tiled layers), 16 bookmarks, 1,783 IAU feature names | ~0.3 MB | NASA Trek index service |
| MOLA MEGDR global 4 ppd (~15 km/px) and 16 ppd (~3.7 km/px) DEMs | 2 + 33 MB | NASA PDS Geosciences Node |
| MOLA MEGDR 128 ppd (463 m/px) 3°×3° patches: Jezero, Gale, Gusev, Meridiani | 4 × 288 KB | NASA PDS, fetched with HTTP range reads (no 130 MB downloads) |
| MGS TES thermal inertia, 8 ppd (~7.5 km/px), numeric | 8.3 MB | ASU Mars Global Data Sets (Christensen et al. 2001) |
| Curiosity & Perseverance traverses + waypoints | ~3 MB | NASA/JPL MMGIS |
| Historic routes (`catalog/historic_routes.geojson`, `--historic`) derived from the above + IAU names | 0.5 MB | NASA/JPL MMGIS, IAU/USGS Gazetteer |
| Pre-cached tiles: 5 global layers z0–3 + landing-site tiles z4–7 | ~38 MB | NASA Trek WMTS |
| Terrain-class layers (`data/terrain/`, shipped; regenerate with `scripts/terrain_map.py`) | ~4 MB grids | this project (see *Terrain classes*) |

Options: `--catalog`, `--dem`, `--ti`, `--vectors`, `--tiles`, `--no-dem16` (skip the 33 MB global 16 ppd DEM).
Anything else you browse while online is cached under `data/tiles/` and then works offline.

## Docker / Compose (recommended)

```bash
scripts/stack.sh up            # map service only -> http://localhost:8080 (fetches data on first run)
scripts/stack.sh up --rover    # + ROS 2 rover twin (Navcam replay, RTAB-Map, learned perception)
scripts/stack.sh up --rover --rerun   # dev only: + Rerun 3D debug viewer (:9090, pre-laid-out)
scripts/stack.sh status        # containers + who owns the ports
scripts/stack.sh down          # stop everything
```

`stack.sh` wraps `docker compose` with a preflight: if a port is taken it names the owner (e.g. a
manually started `autonav0d`) instead of Docker's *"address already in use"* error; use
`AUTONAV_PORT=8081 scripts/stack.sh up` to run side by side. Plain `docker compose --profile rover up -d`
also works. **Rerun is not part of the production stack**: its container only starts with `--rerun`
(compose profile `rerun`), and the UI shows the Rerun link only when `AUTONAV_RERUN_URL` is set.
When an NVIDIA GPU is usable, `stack.sh` adds `compose.gpu.yaml` (learned perception on GPU); otherwise the
rover uses CPU fallbacks.

* Overrides: `AUTONAV_PORT`, `RERUN_WEB_PORT`, `RERUN_GRPC_PORT`, `UID`/`GID` (owner of `./data`),
  `M20_SOLS` (Perseverance sols to replay, default `118:125`), `FRAME_PERIOD_S` (seconds per stereo frame).
* On first start the rover builds a ~9 MB replay log in `data/m20log/` from NASA's raw-image API.
* Images: `autonav0d` (Ubuntu 24.04, ~100 MB, non-root, health-checked) and `autonav0-rover`
  (ROS 2 Jazzy + RTAB-Map + Rerun). `docker build --build-arg BAKE_DATA=1 -t autonav0d .` bakes the data
  pack into the image for stateless deployment.

### Rover twin (ROS 2) — `ros/src/autonav_rover`

| Node | Role |
|---|---|
| `build_m20_log` | Fetches Perseverance Navcam left/right tiles + CAHVORE camera models + rover pose (site frame, attitude) from the public raw-image API, mosaics tiles, **rectifies through the full CAHVORE model** (sub-pixel epipolar error) and georeferences frames via the MMGIS waypoints. |
| `log_player` | Replays the log in real time (ping-pong so the rover revisits places): rectified stereo + `camera_info`, truth pose, **wheel odometry with injected slip/heading drift** (`odom→base_link`), mast camera TF, NavSatFix, joint states for the URDF model. |
| RTAB-Map (`rtabmap_slam`, BSD) | Stereo VSLAM on top of the drifting odometry; loop closures re-localise the rover and publish `map→odom`; `point_cloud_xyzrgb` makes per-frame stereo clouds. |
| `autonav_bridge` | Posts the VSLAM pose (lat/lon, heading, σ, error vs truth and *wheels-only* error) to `autonav0d`; turns `/autonav/goal` (NavSatFix) into `/autonav/path` via `/api/route`. |
| `stereo_depth` | Disparity/depth + point clouds: **RAFT-Stereo** (MIT, GPU, ~1 s/pair, 59–75 % valid pixels) or SGBM + left-right check (CPU fallback, ~0.15 s, ~40 %). |
| `visual_odometry` | Keyframe PnP re-localisation from **XFeat** (Apache-2.0) matches (ORB on CPU) or RAFT/LK optical flow; frames are metres apart, so this acts as re-localisation rather than continuous VO. |
| `rerun_bridge` (dev only, `rerun:=true`) | Streams Navcam images (JPEG) + camera frustum, stereo / SLAM point clouds, truth vs odometry vs VSLAM paths and drift metrics to Rerun, and sends a **blueprint** (3D map · Navcam · error plots · frame log), re-sent periodically so viewers that connect later get the layout. |

The live rover (with uncertainty circle and track) appears on the 2D and 3D maps; with `--rerun` the
header's **Rerun ↗** link opens the point-cloud/VSLAM debug viewer. Memory: RTAB-Map runs with
`Mem/BinDataKept false` and switches to localisation mode after the first replay leg, so the rover stays
around 3–4 GB; the Rerun server is capped at 512 MiB.

## Comm dead spots (default-on layer)

Relay orbiters (MRO, MAVEN, TGO, Mars Express) are only usable above roughly **10° elevation**. Crater
walls, canyon floors and the foot of big mounds block parts of the sky, so fewer relay passes reach the
ground. For every MOLA cell `src/comms.cpp` traces the horizon in 16 directions out to 50 km, including
Mars curvature, and rates the cell by the share of directions still open: **open** (not drawn),
**reduced** (≥75 %, yellow), **poor** (40–75 %, orange) or **dead** (<40 %, red). The grids are computed
once at first start (~4 s for all grids) and cached in `data/terrain/comms/`; `--build-relief` recomputes
them.

The planner uses the layer. The *safe rover* cost gains `+comm_weight × loss` and *astronaut EVA* time is
multiplied by `1 + 2 × comm_weight × loss`, because crews must stay in contact. Loss is 0 / 0.25 / 0.6 / 1
for open / reduced / poor / dead. Route cards show km through dead and poor coverage, and the Tools tab has
an *Avoid comm dead spots* setting (off / normal / strong = `comm_weight` 0 / 1 / 3). Limits: this is
geometry only, not a link budget. It ignores orbit timing, direct-to-Earth geometry, dust and antenna
patterns, and at MOLA resolution (463 m at the sites, 3.7 km elsewhere) narrow gullies and small craters
are not resolved.

## Terrain classes (precomputed layers)

Two hierarchical layers, computed **once** and stored as small categorical grids that the daemon serves as
tile layers, returns in `/api/elevation`, and feeds into the route planner.

| Layer | Level | Classes | Coverage / resolution | How |
|---|---|---|---|---|
| **Relief landforms** (`/dem/relief`) | 1 (shape) | valley/channel, crater floor/basin, plain, slope/wall, mesa/upland, ridge/crater rim, peak | whole planet at 3.7 km (16 ppd); 463 m at the 4 rover sites | Two-scale Topographic Position Index + slope (Weiss 2001 / Jenness 2006) on MOLA; built at first start (~2 s), saved to `data/terrain/relief/` (`autonav0d --build-relief` recomputes) |
| **Surface landforms** (`/dem/terrain`) | group › class | 5 groups (aeolian bedforms, topographic, slope features, impact, basic terrain) › 15 DoMars16k classes (e.g. dunes, cliff, ridge, channel, gullies, mass wasting, crater field, rough/smooth terrain) | 4 rover sites: 60 km at **250 m**; 37 Trek regional CTX mosaics (covering the candidate human Exploration Zones by name): central 100 km at **500 m** | Frozen **DINOv2 ViT-B/14** features + linear head trained on **DoMars16k** (`scripts/terrain_train.py`), applied to ~1.2 km windows of Trek CTX mosaics (`scripts/terrain_map.py`) |

Classifier results on the held-out DoMars16k test split (1,614 images, 15 classes):

| Setting | Test accuracy | Macro-F1 |
|---|---|---|
| ViT-S/14, 1 % of training data (112 images) | 70.8 % | 0.70 |
| ViT-S/14, 10 % | 82.5 % | 0.82 |
| ViT-S/14, 100 % (11,305 images) | 87.5 % | 0.87 |
| ViT-B/14, 100 % | **89.5 %** | 0.89 |
| ViT-B/14 on imagery degraded to Trek z12 (~10 m/px) — used for rover sites | 89.0 % | 0.89 |
| ViT-B/14 on imagery degraded to Trek z11 (~20 m/px) — used for EZ regions | 86.7 % | 0.86 |

(An earlier few-shot attempt that labelled sand/bedrock/rocks/soft soil from Curiosity's traverse reached
only ~40 % on held-out sol ranges and was dropped.) Weakest classes: textured terrain and ridge (F1 ≈ 0.7).

Each class carries a heuristic **planner effect** (`planner` table in the grid JSON, editable): aeolian
bedforms = soft (sand-trap), cliffs/rough terrain/mass wasting = rocky, smooth terrain = benign. Mapped
classes raise the TI-based rock/soft costs where present. Outputs per region: `terrain_<region>_<tier>.json`
+ `.img` (int16, 0 = no data, 16 = uncertain when p < 0.4), a GeoTIFF copy (`.tif`, lat/lon on the
3396.19 km sphere) for GIS use, and a preview JPG.

Regenerate (GPU recommended, ~4 min per region on an RTX 5060; downloads Trek tiles into a disposable cache):

```bash
docker compose -f compose.yaml -f compose.gpu.yaml --profile terrain run --rm terrain-map            # all
docker compose -f compose.yaml -f compose.gpu.yaml --profile terrain run --rm terrain-map --tier sites --regions Gale
# retrain the head (needs the DoMars16k parquet files from HF Mirali33/mb-domars16k in ./domars):
docker run --rm --gpus all -v $PWD/data:/data -v $PWD/scripts:/scripts:ro -v $PWD/domars:/ds -e AUTONAV_DATA=/data \
  --entrypoint /opt/perception-venv/bin/python autonav0-rover:latest /scripts/terrain_train.py --dataset /ds --backbone vitb14 --scale 0.6
```

### Deploying to Google Cloud / GKE (later stage)

Both services are 12-factor friendly: configuration by flags/env, stateless HTTP, health endpoint
(`/api/health`), non-root users, data either baked (`BAKE_DATA=1`) or on a volume.
Suggested mapping: push images to Artifact Registry → `autonav0d` Deployment (+ HPA, readiness probe on
`/api/health`, `--offline` if egress is restricted) behind an Ingress; `rerun` Deployment with a Service for
`9090`/`9876`; `rover` as a single-replica Deployment (ROS 2 DDS stays inside the pod). **Before exposing
publicly** add authentication in front of the Ingress (e.g. IAP): `POST /api/rover/state` and `POST /api/mode`
are unauthenticated by design for local use. Pass `--rerun-url` with the public Rerun URLs.

## Trek feature parity (2D)

| Mars Trek feature | AutoNav-0 Lite |
|---|---|
| Layer browser (mosaics, DEMs, mineralogy, thermal inertia…) with search, opacity, zoom-to | ✅ 165 Trek WMTS layers, filter/in-view, opacity, stacking |
| Basemap switching | ✅ global Trek layers |
| Bookmarks (landing sites, *The Martian* path) that switch on their layers | ✅ |
| Feature-name search & labels (IAU nomenclature) | ✅ |
| Rover traverses / waypoints | ✅ MSL + M2020 (MMGIS), sol popups |
| Elevation readout, profile & distance tool | ✅ cursor elevation, multi-point profile chart |
| Sun angle / lighting | ✅ Mars24 clock, sun az/el sliders, server hillshade **with cast shadows** |
| Slope analysis | ✅ slope hazard overlay (configurable limit), contours |
| Path planning | ➕ A* *safe rover / wheel saver / astronaut EVA / shortest*, slope limits, EVA hours, rover sols & energy, wheel-wear index, odometry-drift σ circles and re-localisation stops |
| Historic routes | ➕ 30 routes in the layer browser: Curiosity & Perseverance traverses (full + ~3 km legs, sol ranges) and Opportunity milestones; **Plan** loads start/end into the planner and scores the actually-driven path with the same model ("Actually driven" card) |
| Route comparison | ➕ Google-Maps-style: all profiles + diverse alternatives, "best for" badges, deltas, click-to-select, overlaid elevation profiles |
| 3D view | ✅ three.js terrain from the DEM, draped with the current 2D layers, sun-angle shadows, routes in 3D, fly-along |
| Terrain classification | ➕ relief landforms planet-wide + DoMars16k surface landforms (41 regions), legends, cursor readout, used by the planner |
| Live rover / perception | ➕ ROS 2 rover twin (Perseverance Navcam replay), RTAB-Map VSLAM, learned stereo/features; Rerun point clouds (dev) |
| Offline use | ➕ cache-first daemon, `--offline`, Unix-socket IPC |
| 3D globe, STL export, KML/shape import | ⏳ roadmap |

## HTTP API (all GET unless noted)

| Endpoint | Purpose |
|---|---|
| `/api/health` | status, online flag, cache counters, loaded DEMs |
| `POST /api/mode?offline=0\|1` | toggle upstream tile fill (orbiter "uplink") |
| `/api/layers`, `/api/bookmarks`, `/api/nomenclature?q=gale&limit=25` | catalogs |
| `/api/vector/{msl_traverse,msl_waypoints,m20_traverse,m20_waypoints}` | GeoJSON |
| `/api/elevation?lat=&lon=` | best-resolution MOLA elevation + TES thermal inertia + `relief`, `terrain` (group › class) and `comms` coverage class |
| `/api/terrain/classes` | terrain-class and comm-coverage grids: extent, resolution, class names/colours/groups/planner effects |
| `/api/profile?pts=lat,lon;lat,lon[;…]&n=256` | elevation/slope profile |
| `/api/route?from=lat,lon&to=lat,lon&profile=safe\|wear\|eva\|shortest&max_slope=25&drift=0.02&reloc_sigma=50&m_per_sol=150&grid=400&comm_weight=1` | GeoJSON route + telemetry (incl. `comm_dead_km`, `comm_poor_km`) |
| `/api/routes?from=..&to=..&profiles=safe,wear,eva,shortest&alternatives=0..3[&ref=<historic id>]` | route comparison (FeatureCollection + `best` per metric); `ref` adds the historic traverse scored with the same model |
| `/api/historic` | historic rover routes (GeoJSON: id, rover, sols, start/end, distance) |
| `/api/terrain?s=&w=&n=&e=&size=256` | heightmap for the 3D view |
| `GET\|POST\|DELETE /api/rover/state` | live rover pose/σ/track (pushed by the ROS 2 bridge) |
| `/api/sun?lat=&lon=[&t=unix]` | MSD, Ls, LMST/LTST, sun elevation/azimuth |
| `/tiles/{layer}/{z}/{row}/{col}.{png\|jpg}` | Trek EQ WMTS tile (cache → upstream) |
| `/dem/{hillshade\|slope\|contours}/{z}/{row}/{col}.png?az=&el=&shadows=1&max_slope=&interval=` | DEM-derived rasters |
| `/dem/{relief\|terrain\|comms}/{z}/{row}/{col}.png` | colour-coded terrain-class / comm dead-spot tiles |

Longitudes are degrees **east, −180…180**; tiles use Trek's equirectangular grid (z0 = 2×1 tiles of 256 px),
which matches Leaflet's `EPSG:4326` scheme.

## Model assumptions & limits (be honest with the crew)

* Elevation is MOLA MEGDR (463 m/px at sites, ~15 km/px elsewhere). Slopes are therefore smoothed and
  **rocks, ventifacts and sand ripples are invisible** — routes are strategic planning aids only.
  Upgrade path: HiRISE/CTX DTMs (1–20 m) as additional `data/dem/*.json+.img` grids (int16 BE today; GDAL
  ingest is on the roadmap).
* Safe-rover cost = 3D distance × (1 + 4·(slope/limit)² + 3·soft), hard stop above the limit; EVA cost = walking time
  from Tobler's hiking function × 0.5 (suit penalty); shortest = 3D distance with a 45° physical limit.
* **Wheel saver / wear index** (same model for every route so comparisons are fair): per edge
  3D distance × (1 + 2·(slope/limit)² + 3·roughness + 2·rocky + 0.5·soft) plus 0.6 cell-lengths per 45° of
  heading change (turning scrub; A* tracks heading). Roughness = detrended DEM curvature; *rocky* = TES
  thermal inertia 300→500 (rock/bedrock-dominated; Curiosity's wheel damage came from such terrain);
  *soft* = TI 150→50 (dust / loose fines, sand-trap risk, Research.md §1). Reported in wear-equivalent km.
  TES is 7.5 km/px: it flags *regional* rockiness, not individual ventifacts.
* Rover energy: MSL-class 1,025 kg, rolling resistance 0.15, 25 % drivetrain efficiency, Mars g = 3.721 m/s².
* Odometry drift σ = drift% × distance since last fix; a re-localisation stop is inserted when σ exceeds the
  threshold (see `Research.md` §3).

* Terrain classes describe ~1.2 km windows: features narrower than that (e.g. the ~1 km-wide dark Bagnold
  dune band at Gale) are often labelled by their surroundings ("textured/mixed terrain"). The classes are
  orbital morphology, not ground truth for wheel safety; treat the planner effects as hints.
* Historic comparisons: the driven path is resampled onto the planning grid (~230 m cells at 463 m DEM),
  so its distance is a little shorter than the logged odometry. Where it is still longer than the planned
  route, the rover was avoiding hazards smaller than a DEM pixel — exactly what orbital-only planning misses.
  Opportunity routes are milestones only (no public traverse file): straight segments, endpoint markers.
* Rover twin: Navcam CAHVORE models are rectified to ideal pinholes; RTAB-Map uses the replayed frames
  (seconds apart, minutes apart in reality) with drifting wheel odometry — this demonstrates drift
  correction by loop closure, not flight-grade VO.

## Licensing

Source code: choose a licence for the repo (MIT/Apache-2.0 recommended — all bundled third-party code is
permissive). Third-party components and data credits are listed in [CITATIONS.md](CITATIONS.md).
