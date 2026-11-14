# AutoNav-0: Offline Low-Power AI Navigation & Martian Cartography
**Target Platform:** Ubuntu Linux (Headless C++ Daemon `autonav0d` + Modular 2D/3D GUI `autonav0-ui`)  
**Challenge:** NASA Space Apps Challenge — *Interplanetary Survival Guide: Martian Map*

---

## Executive Summary & Core Architecture
**AutoNav-0** is an open-source, desktop-native Martian Geospatial Information System (GIS) and autonomous rover/astronaut path-planning suite. Unlike browser-bound tools (such as NASA Trek) that require continuous connectivity and hit memory ceilings, **AutoNav-0** runs natively on Ubuntu as a decoupled, low-power service. 

It directly addresses historical Martian surface mission bottlenecks (*Spirit* & *Opportunity* sand traps, *Curiosity* wheel punctures, and *Perseverance* odometry drift and irregular orbiter relay windows) by combining offline memory-resident orbital maps with local edge-AI terrain reconstruction.

* **2D Low-Power Mode (Offline Core):** Minimal CPU/GPU footprint using pre-cached HiRISE/MOLA/THEMIS raster tiles, solar angle illumination, and risk-aware $A^*$ routing.
* **3D High-Performance Mode (Edge AI):** GPU-accelerated terrain reconstruction fusing zero-shot monocular depth point clouds / Gaussian Splats onto orbital Digital Terrain Models (DTMs) to preview occluded hazards.

---

## Priority 1: Critical (Must-Have for Hackathon Submission / MVP)
*These deliverables form the core working executable required to demonstrate an offline, low-power NASA Trek alternative with risk-aware path planning.*

### 1.1 Ubuntu Headless Service & 2D Viewport (`autonav0d` + `autonav0-ui`)
* **Task 1.1.1:** Set up C++ build system (CMake) on Ubuntu targeting a standalone executable / AppImage.
* **Task 1.1.2:** Implement headless background daemon (`autonav0d`) handling raster tile loading, coordinate transformations, and pathfinding math.
* **Task 1.1.3:** Build lightweight 2D UI client (`Qt6`, `Dear ImGui`, or `Godot 2D`) communicating with `autonav0d` via local IPC (Unix Domain Sockets / Shared Memory).

### 1.2 Offline Memory-Resident Martian Map Layers
* **Task 1.2.1:** Ingest and cache NASA PDS / GeoTIFF elevation models (MOLA global DEM + localized HiRISE DTMs) directly into system RAM for zero-latency offline panning and zooming.
* **Task 1.2.2:** Implement multi-layer toggle controls (Elevation Contour Lines, Orthomosaic Imagery, Slope Gradient Heatmap).
* **Task 1.2.3:** Add **"You Are Here"** positioning with quick-jump bookmarks to historical NASA landing sites:
  * **Jezero Crater** (*Perseverance* — `18.44°N, 77.45°E`)
  * **Gale Crater** (*Curiosity* — `4.59°S, 137.44°E`)
  * **Gusev Crater** (*Spirit* — `14.57°S, 175.47°E`)
  * **Meridiani Planum** (*Opportunity* — `1.95°S, 354.47°E`)

### 1.3 Risk-Aware 2D Path Planning Engine
* **Task 1.3.1:** Implement $A^*$ / Theta* pathfinding over the cached elevation grid.
* **Task 1.3.2:** Add multi-objective route profiles:
  * **Geodesic / Shortest Path:** Minimizes pure Euclidean distance.
  * **Safe / Rover-Preserving Path:** Penalizes slopes $>25^\circ\text{--}30^\circ$ and high-roughness terrain (preventing *Curiosity*-style wheel damage).
* **Task 1.3.3:** Display route telemetry readout (Total Distance, Max Slope Angle, Estimated Sols/Energy Cost).

### 1.4 Dynamic Solar Position & Illumination Toggle
* **Task 1.4.1:** Implement a Solar Azimuth/Elevation slider and toggle in the 2D HUD.
* **Task 1.4.2:** Compute real-time hillshading and directional shadow casting across the DTM so users can identify sunlit charging corridors versus freezing shadowed craters.

---

## Priority 2: High Value (Next-Gen AutoNav & Mission Realism)
*These features elevate the app from a map viewer to an authentic autonomous surface survival tool grounded in JPL AutoNav research.*

### 2.1 Semantic Terrain Costmap (Sand-Trap & Ventifact Avoidance)
* **Mission Evidence:** *Spirit* (Troy) and *Opportunity* (Purgatory Dune) became trapped in low-thermal-inertia powdery sand ripples invisible to pure slope maps.
* **Task 2.1.1:** Overlay THEMIS Thermal Inertia data or run a lightweight texture/color classifier on cached orbital tiles to tag **Bedrock (Safe)**, **Sand Ripples (High Slip Risk)**, and **Boulder Fields (Wheel Hazard)**.
* **Task 2.1.2:** Feed semantic hazard weights into the 2D $A^*$ cost function so routes automatically skirt around sand traps.

### 2.2 Multi-Sol Waypoint Chaining & Odometry Uncertainty Ellipses
* **Mission Evidence:** JPL's Next-Gen AutoNav prioritizes extending drives beyond the 50–100m line-of-sight bubble while managing visual odometry drift.
* **Task 2.2.1:** Allow users to chain long-range multi-sol waypoints behind ridges/craters, automatically subdividing routes into ~50m line-of-sight segments.
* **Task 2.2.2:** Render an expanding **Position Uncertainty Ellipse** around the rover during simulated traverses; trigger a mandatory re-localization stop when the ellipse touches a hazard boundary.

### 2.3 Simulated Orbiter Relay & Telemetry Buffer (Online Sync Mode)
* **Mission Evidence:** Irregular orbiter passes (MRO/MAVEN) force rovers to buffer telemetry offline and prioritize downlinks.
* **Task 2.3.1:** Add an **Orbiter Uplink Toggle** (backed by a local WiFi connectivity check on Ubuntu).
* **Task 2.3.2:** Queue generated routes and local logs in a priority ring buffer while offline, flushing and pulling fresh PDS/HiRISE patches when the simulated orbiter pass is active.

---

## Priority 3: Advanced / Edge AI (3D High-Performance Mode)
*These features showcase cutting-edge local GPU inference and real-time 3D visualization.*

### 3.1 2D-to-3D Viewport Mode Switch
* **Task 3.1.1:** Build a hardware-accelerated 3D terrain renderer (Vulkan/OpenGL or Godot 3D) that displaces a mesh using the cached DTM.
* **Task 3.1.2:** Render the computed 3D path curve, rover/astronaut marker, and dynamic sun shadows in 3D space with free-camera flythrough controls.

### 3.2 Real-Time Zero-Shot Monocular Depth Point Clouds
* **Mission Evidence:** Orbital HiRISE (~25 cm/px) cannot resolve sharp $>8\text{ cm}$ ventifact rocks that puncture rover wheels.
* **Task 3.2.1:** Integrate a lightweight Zero-Shot Depth model (e.g., *Depth Anything v2* or *Metric3D* via ONNX Runtime / TensorRT on Ubuntu) to infer depth maps from single rover Navcam/Hazcam images.
* **Task 3.2.2:** Back-project the inferred depth map into a 3D point cloud (`x, y, z, r, g, b`) and render it ahead of the rover in 3D mode to preview micro-terrain obstacles.

---

## Priority 4: Optional (Stretch Goals & Polish)
*Nice-to-have additions if time permits before final submission.*

### 4.1 Uncalibrated Perseverance Image Rectification & Geotagging
* **Task 4.1.1:** Parse raw NASA Mars 2020 PDS/JSON image metadata (camera azimuth, elevation, rover odometry).
* **Task 4.1.2:** Use feature matching (ORB/SIFT) or ray-casting onto the local DTM mesh to project and pin raw ground-level rover photos directly onto their exact coordinates on the map.

### 4.2 Grounded 3D Gaussian Splatting / Terrain Extrapolation
* **Task 4.2.1:** Fuse orbital orthomosaic textures with DTM elevation and local depth point clouds to generate grounded, non-hallucinated 3D terrain previews for occluded valleys.

### 4.3 Astronaut EVA Consumable Isochrones
* **Task 4.3.1:** Add a toggleable "Astronaut EVA Mode" that replaces rover wheel-wear limits with oxygen/battery burn rates based on slope steepness, drawing a maximum safe return-to-base radius.