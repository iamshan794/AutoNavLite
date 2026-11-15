# Citations, data sources & licences

AutoNav-0 Lite only uses openly licensed software and public NASA / USGS / PDS data. It does **not**
copy NASA Trek's source code; it re-implements Trek-style features and consumes Trek's public WMTS/index
services, with credit shown in the map attribution.

## Data
https://www-robotics.jpl.nasa.gov/what-we-do/research-tasks/next-gen-autonav-concept/

| Data | Producer / host | Terms | Used for |
|---|---|---|---|
| NASA Solar System Treks — Mars Trek WMTS tiles & layer/bookmark/nomenclature index (`trek.nasa.gov`) | NASA SSTP / JPL-Caltech | NASA content, generally not copyrighted ([NASA media guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/)); credit "NASA/JPL-Caltech, Solar System Treks" | Basemaps, overlays, catalog, bookmarks |
| MGS MOLA Mission Experiment Gridded Data Records (MEGDR), `MGS-M-MOLA-5-MEGDR-L3-V1.0` | NASA PDS Geosciences Node; Smith, D. et al. (2003) | Public PDS archive | Elevation, slope, hillshade, A* routing |
| Gazetteer of Planetary Nomenclature (via Trek index) | IAU WGPSN / USGS Astrogeology | Public domain (USGS) | Feature names & search |
| Mars Science Laboratory & Mars 2020 traverses/waypoints (`mars.nasa.gov/mmgis-maps`) | NASA/JPL-Caltech MMGIS | NASA public | Rover traverse layers; historic routes (split into legs, sol ranges matched to waypoints) |
| Viking MDIM 2.1 colour mosaic | USGS Astrogeology / NASA | Public domain | Default basemap |
| THEMIS-IR day/night mosaics | ASU / NASA Mars Odyssey (Edwards et al., 2011; Hill et al.) | NASA public | Overlay / basemap |
| TES thermal inertia, albedo, mineral maps | ASU / NASA MGS (Christensen et al., 2001; Putzig et al.) | NASA public | Overlays (sand-trap context) |
| CTX / HiRISE mosaics & DTMs listed by Trek | MSSS, U. Arizona (HiRISE), NASA/JPL MRO | NASA public; credit "NASA/JPL/MSSS" and "NASA/JPL/University of Arizona" | Optional overlays |
| HRSC-derived layers listed by Trek | ESA/DLR/FU Berlin | **CC BY-SA 3.0 IGO** — attribute "ESA/DLR/FU Berlin" and share-alike | Optional overlays (not prefetched) |
| MGS TES thermal inertia (numeric, 8 ppd) | Christensen et al. (2001) JGR 106(E10); ASU Mars Global Data Sets (mars.asu.edu/data/tes_ti) | NASA-funded public data; cite Christensen et al. 2001 | Wheel-wear (rocky) and sand-trap (soft) costs |
| Mars 2020 Perseverance Navcam raw images + CAHVORE camera models + rover pose metadata | NASA/JPL-Caltech raw-image API (mars.nasa.gov/mars2020/multimedia/raw-images) | NASA public; credit "NASA/JPL-Caltech" | Rover twin replay, stereo, VSLAM |
| MOC Atlas mosaic | MSSS / NASA MGS | NASA/JPL/MSSS credit | Optional overlay (not prefetched) |
| MRO CTX regional block-adjusted mosaics (`*_CTX_BlockAdj_dd`, 37 regions) via Trek | MSSS / NASA/JPL MRO (Malin et al. 2007, JGR 112, E05S04); NASA Solar System Treks | NASA public; credit "NASA/JPL/MSSS" | Input imagery for the surface-landform layers |
| **DoMars16k** — Wilhelm, T. et al. (2020), *DoMars16k: A Diverse Dataset for Weakly Supervised Geomorphologic Analysis on Mars*, Remote Sens. 12(23), 3981, doi:10.3390/rs12233981; data doi:10.5281/zenodo.4291940 | TU Dortmund | **CC BY 4.0** | Training/test data for the landform classifier (used via the Hugging Face repackaging `Mirali33/mb-domars16k`, revision `4d79cf7a`, CC BY 4.0; train/val/test splits) |
| AutoNav-0 terrain-class grids (`data/terrain/`) | this project, derived from the above | Derived data: keep the CC BY 4.0 attribution to DoMars16k and NASA/JPL/MSSS credit | Relief/surface landform layers, planner costs |

Per-layer credit (mission/instrument) is shown in the map attribution and stored in `layers.json`.

## Algorithms

* Allison, M. & McEwen, M. (2000). *A post-Pathfinder evaluation of areocentric solar coordinates with
  improved timing recipes for Mars seasonal/diurnal climate studies.* Planet. Space Sci. 48, 215–235;
  implemented per [NASA GISS Mars24 algorithm notes](https://www.giss.nasa.gov/tools/mars24/help/algorithm.html)
  and verified against its worked examples (`tests/test_core.cpp`).
* Yakimovsky, Y. & Cunningham, R. (1978) and Gennery, D. B. (2006), *Generalized camera calibration including
  fish-eye lenses* (CAHV/CAHVOR/CAHVORE). The CAHVORE projection in `ros/.../cahv.py` is ported from
  [MarsRaw/sciimg](https://github.com/MarsRaw/sciimg) `src/camera/cahvore.rs` (MIT, © 2021 Kevin M. Gill).
* Labbé, M. & Michaud, F. (2019). *RTAB-Map as an open-source lidar and visual SLAM library*, J. Field Robotics.
* Tobler, W. (1993). *Three presentations on geographical analysis and modeling* (hiking function).
* Hart, Nilsson & Raphael (1968). A* search.
* Weiss, A. (2001). *Topographic Position and Landforms Analysis*, ESRI User Conference poster; Jenness, J.
  (2006) *Topographic Position Index* — two-scale TPI landform classes (`src/terrain.cpp`).
* Oquab, M. et al. (2023). *DINOv2: Learning Robust Visual Features without Supervision*, arXiv:2304.07193 (TMLR 2024) — frozen
  backbone for the landform classifier (`facebookresearch/dinov2` at commit `7764ea0f`, Apache-2.0 code
  and weights).
* Lipson, L., Teed, Z. & Deng, J. (2021). *RAFT-Stereo*, 3DV — learned stereo (MIT, commit `6e93ed2`).
* Potje, G. et al. (2024). *XFeat: Accelerated Features for Lightweight Image Matching*, CVPR (Apache-2.0,
  commit `e92685f`).
* Teed, Z. & Deng, J. (2020). *RAFT: Recurrent All-Pairs Field Transforms for Optical Flow*, ECCV (torchvision
  implementation, BSD-3-Clause).
* Mission lessons motivating the cost model: see `Research.md` (MER Purgatory/Troy, MSL wheel wear, M2020 ENav).

## Software (vendored, unmodified)

| Component | Version | Licence |
|---|---|---|
| [cpp-httplib](https://github.com/yhirose/cpp-httplib) | 0.18.3 | MIT (`third_party/httplib/LICENSE`) |
| [nlohmann/json](https://github.com/nlohmann/json) | 3.11.3 | MIT (`third_party/nlohmann/LICENSE.MIT`) |
| [stb_image_write](https://github.com/nothings/stb) | master | MIT / Public domain (licence at end of header) |
| [Leaflet](https://leafletjs.com) | 1.9.4 | BSD-2-Clause (`web/vendor/leaflet/LICENSE`) |
| libcurl (system, optional) | — | curl licence (MIT-style) |
| [three.js](https://threejs.org) (+ OrbitControls) | r170 | MIT (`web/vendor/three/LICENSE`) |
| [ROS 2 Jazzy](https://docs.ros.org/en/jazzy/) (Docker `ros:jazzy-ros-base`) | Jazzy | Apache-2.0 |
| [RTAB-Map / rtabmap_ros](https://github.com/introlab/rtabmap_ros) (apt) | Jazzy binaries | BSD-3-Clause |
| [Rerun SDK & viewer](https://rerun.io) (pip, isolated venv; analytics disabled) | 0.43.2 | MIT / Apache-2.0 |
| OpenCV (Ubuntu apt) | 4.6 | Apache-2.0 |
| CycloneDDS (`rmw_cyclonedds_cpp`) | Jazzy | EPL-2.0 / EDL-1.0 |
| PyTorch / torchvision (pip, isolated venv) | 2.11.0 / 0.26.0 (cu128) | BSD-3-Clause |
| opt_einsum, tqdm | pip | MIT / MPL-2.0 & MIT |
| Apache Arrow `pyarrow` (training data reader) | 17.0.0 | Apache-2.0 |

Licence choices: models with non-commercial or unclear weight licences (e.g. FoundationStereo,
SuperPoint/SuperGlue, SegFormer/MiT, DINOv3) were deliberately not used.
