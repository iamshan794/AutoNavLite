// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — 3D terrain view (three.js). Heightmap comes from autonav0d (/api/terrain), the texture
// is composited from the same tiles shown in the 2D map, lighting follows the Mars sun sliders.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (s) => document.querySelector(s);
const MARS_R = 3396190;
const DEG = Math.PI / 180;
const MAX_SPAN_DEG = 12;       // keeps meshes small (MOLA patches are 3 deg; global DEM ~15 km/px)
const MAX_TEX = 4096;

const nav = window.autonav;
const el = $('#view3d');
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
el.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a0f0a);
scene.fog = new THREE.Fog(0x1a0f0a, 1e6, 1e6);
const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 5000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = false;
controls.maxPolarAngle = Math.PI * 0.495;
const hemi = new THREE.HemisphereLight(0xffd9b0, 0x2a1408, 0.35);
const sunLight = new THREE.DirectionalLight(0xfff1dc, 2.2);
sunLight.castShadow = true;
sunLight.shadow.mapSize.set(4096, 4096);
sunLight.shadow.bias = -0.0005;
sunLight.shadow.normalBias = 0.02;
scene.add(hemi, sunLight, sunLight.target);

const state = { terrain: null, mesh: null, routes: new THREE.Group(), markers: new THREE.Group(),
  exag: 3, fly: null, active: false };
scene.add(state.routes, state.markers);

// ------------------------------------------------------------------ render on demand (low power)
let pending = false;
function requestRender() {
  if (pending || !state.active) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; renderer.render(scene, camera); });
}
controls.addEventListener('change', requestRender);
function resize() {
  const w = el.clientWidth, h = el.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  requestRender();
}
new ResizeObserver(resize).observe(el);

// ------------------------------------------------------------------ geometry helpers (units: km)
function project(lat, lon) {
  const t = state.terrain;
  const [s, w, n, e] = t.bounds;
  const x = ((lon - w) / (e - w) - 0.5) * t.width_m / 1000;
  const z = ((n - lat) / (n - s) - 0.5) * t.height_m / 1000;  // north = -Z
  return [x, z];
}
function heightAt(lat, lon) {  // bilinear in the heightmap, metres
  const t = state.terrain;
  const [s, w, n, e] = t.bounds;
  const fx = Math.min(Math.max((lon - w) / (e - w) * (t.width - 1), 0), t.width - 1.001);
  const fy = Math.min(Math.max((n - lat) / (n - s) * (t.height - 1), 0), t.height - 1.001);
  const x0 = Math.floor(fx), y0 = Math.floor(fy), tx = fx - x0, ty = fy - y0;
  const Z = (y, x) => t.elev[y * t.width + x];
  return (Z(y0, x0) * (1 - tx) + Z(y0, x0 + 1) * tx) * (1 - ty) + (Z(y0 + 1, x0) * (1 - tx) + Z(y0 + 1, x0 + 1) * tx) * ty;
}
const yOf = (m) => (m - state.terrain.min) / 1000 * state.exag;

// ------------------------------------------------------------------ texture from 2D map layers
function tileUrl(layer, z, x, y) {
  return L.Util.template(layer._url, Object.assign({}, layer.options, { z, x, y }));
}
async function buildTexture(bounds, status) {
  const [s, w, n, e] = bounds;
  const layers = [nav.getBasemap(), ...nav.getOverlays(), ...nav.getDemOverlays()
    .filter((l) => !l._url.includes('/hillshade/'))].filter(Boolean);
  // Zoom giving ~2048 px across the view, capped by texture size.
  let z = Math.ceil(Math.log2(2048 / (e - w) * 180 / 256));
  z = Math.max(0, Math.min(z, 14));
  while (z > 0 && Math.max((e - w), (n - s)) * 256 * 2 ** z / 180 > MAX_TEX) z--;
  const ppd = 256 * 2 ** z / 180;
  const cw = Math.max(64, Math.round((e - w) * ppd)), ch = Math.max(64, Math.round((n - s) * ppd));
  const canvas = document.createElement('canvas');
  canvas.width = cw; canvas.height = ch;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#5a3a28'; ctx.fillRect(0, 0, cw, ch);
  let loaded = 0, total = 0;
  for (const layer of layers) {
    const lz = Math.min(z, layer.options.maxNativeZoom ?? z);
    const span = 180 / 2 ** lz;
    const lb = layer.options.bounds;
    const x0 = Math.max(0, Math.floor((w + 180) / span)), x1 = Math.min(2 ** (lz + 1) - 1, Math.floor((e + 180) / span));
    const y0 = Math.max(0, Math.floor((90 - n) / span)), y1 = Math.min(2 ** lz - 1, Math.floor((90 - s) / span));
    const jobs = [];
    for (let ty = y0; ty <= y1; ty++) for (let tx = x0; tx <= x1; tx++) {
      const tw = -180 + tx * span, tn = 90 - ty * span;
      if (lb && (tw > lb.getEast() || tw + span < lb.getWest() || tn < lb.getSouth() || tn - span > lb.getNorth())) continue;
      total++;
      jobs.push(new Promise((res) => {
        const img = new Image();
        img.onload = () => res({ img, tw, tn });
        img.onerror = () => res(null);
        img.src = tileUrl(layer, lz, tx, ty);
      }).then((r) => { status(`texture ${++loaded}/${total}`); return r; }));
    }
    const tiles = await Promise.all(jobs);
    ctx.globalAlpha = layer.options.opacity ?? 1;
    for (const t of tiles) {
      if (!t) continue;
      ctx.drawImage(t.img, (t.tw - w) * ppd, (n - t.tn) * ppd, span * ppd + 0.5, span * ppd + 0.5);
    }
  }
  ctx.globalAlpha = 1;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return { tex, z };
}

// ------------------------------------------------------------------ terrain mesh
function applyHeights() {
  const t = state.terrain, pos = state.mesh.geometry.attributes.position;
  for (let i = 0; i < pos.count; i++) pos.setY(i, yOf(t.elev[i]));
  pos.needsUpdate = true;
  state.mesh.geometry.computeVertexNormals();
  state.mesh.geometry.computeBoundingSphere();
}

async function load() {
  const status = (m) => { $('#v3-status').textContent = m; };
  const b = nav.map.getBounds(), c = nav.map.getCenter();
  let s = Math.max(b.getSouth(), -89), n = Math.min(b.getNorth(), 89);
  let w = Math.max(b.getWest(), -180), e = Math.min(b.getEast(), 180);
  if (n - s > MAX_SPAN_DEG) { s = Math.max(-89, c.lat - MAX_SPAN_DEG / 2); n = Math.min(89, c.lat + MAX_SPAN_DEG / 2); }
  if (e - w > MAX_SPAN_DEG * 1.6) { w = Math.max(-180, c.lng - MAX_SPAN_DEG * 0.8); e = Math.min(180, c.lng + MAX_SPAN_DEG * 0.8); }
  status('loading heightmap…');
  const size = +$('#v3-res').value;
  const r = await fetch(`api/terrain?s=${s}&w=${w}&n=${n}&e=${e}&size=${size}`);
  const t = await r.json();
  if (!r.ok) throw new Error(t.error || 'terrain failed');
  state.terrain = t;
  const wk = t.width_m / 1000, hk = t.height_m / 1000;
  // Auto exaggeration: make relief ~8 % of the scene width (Mars terrain looks flat at 1x).
  if (!state.userExag) {
    state.exag = Math.min(10, Math.max(1, Math.round(0.08 * Math.max(wk, hk) / Math.max(0.05, (t.max - t.min) / 1000))));
    $('#v3-exag').value = state.exag; $('#v3-exag-v').textContent = state.exag;
  }
  status('loading texture…');
  const { tex, z } = await buildTexture(t.bounds, status);
  if (state.mesh) { scene.remove(state.mesh); state.mesh.geometry.dispose(); state.mesh.material.map?.dispose(); state.mesh.material.dispose(); }
  const geo = new THREE.PlaneGeometry(wk, hk, t.width - 1, t.height - 1);
  geo.rotateX(-Math.PI / 2);  // XY plane -> XZ; first row ends up at -Z (north)
  state.mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ map: tex, roughness: 1, metalness: 0 }));
  state.mesh.castShadow = state.mesh.receiveShadow = true;
  scene.add(state.mesh);
  applyHeights();

  const span = Math.max(wk, hk);
  camera.near = span / 2000; camera.far = span * 20; camera.updateProjectionMatrix();
  const cy = yOf((t.min + t.max) / 2);
  controls.target.set(0, cy, 0);
  camera.position.set(0, cy + span * 0.55, span * 0.75);
  controls.update();
  const sh = sunLight.shadow.camera;
  sh.left = -span * 0.75; sh.right = span * 0.75; sh.top = span * 0.75; sh.bottom = -span * 0.75;
  sh.near = 0.01; sh.far = span * 6; sh.updateProjectionMatrix();
  updateSun();
  drawRoutes();
  status(`DEM ${t.dem_res_m} m/px · ${t.width}×${t.height} · texture z${z} · ${wk.toFixed(0)}×${hk.toFixed(0)} km`);
  requestRender();
}

// ------------------------------------------------------------------ sun
function updateSun() {
  if (!state.terrain) return;
  const az = +$('#sun-az').value * DEG, elv = +$('#sun-el').value * DEG;
  const span = Math.max(state.terrain.width_m, state.terrain.height_m) / 1000;
  const dir = new THREE.Vector3(Math.sin(az) * Math.cos(elv), Math.sin(elv), -Math.cos(az) * Math.cos(elv));
  sunLight.target.position.copy(controls.target);
  sunLight.position.copy(controls.target).addScaledVector(dir, span * 2);
  sunLight.intensity = elv > 0 ? 2.2 : 0;
  hemi.intensity = elv > 0 ? 0.35 : 0.08;
  requestRender();
}
['#sun-az', '#sun-el'].forEach((id) => $(id).addEventListener('input', updateSun));
$('#sun-now').addEventListener('click', () => setTimeout(updateSun, 400));

// ------------------------------------------------------------------ routes, markers, fly-along
const COLORS = { safe: 0x7ddc7d, wear: 0xff9f1c, eva: 0xc77dff, shortest: 0xffd166 };
function routeCurve(f) {
  const pts = [];
  const lift = Math.max(state.terrain.width_m, state.terrain.height_m) / 1000 / 600;
  for (const [lon, lat] of f.geometry.coordinates) {
    const [x, z] = project(lat, lon);
    pts.push(new THREE.Vector3(x, yOf(heightAt(lat, lon)) + lift, z));
  }
  return pts;
}
function clearGroup(g) {
  for (const o of [...g.children]) { g.remove(o); o.geometry?.dispose(); o.material?.dispose(); }
}
function drawRoutes() {
  clearGroup(state.routes); clearGroup(state.markers);
  if (!state.terrain) return;
  const rs = nav.routesState, span = Math.max(state.terrain.width_m, state.terrain.height_m) / 1000;
  const [s, w, n, e] = state.terrain.bounds;
  for (const f of rs.features) {
    if (!f.geometry.coordinates.every(([lon, lat]) => lat >= s && lat <= n && lon >= w && lon <= e)) continue;
    const pts = routeCurve(f);
    if (pts.length < 2) continue;
    const sel = f.id === rs.selected;
    const curve = new THREE.CatmullRomCurve3(pts, false, 'centripetal', 0.1);
    const tube = new THREE.TubeGeometry(curve, Math.min(2000, pts.length * 3), span / (sel ? 500 : 900), 6, false);
    const mat = new THREE.MeshBasicMaterial({ color: f.properties.reference ? 0xffffff : COLORS[f.properties.profile] ?? 0xcccccc,
      transparent: !sel, opacity: sel ? 1 : 0.55 });
    const mesh = new THREE.Mesh(tube, mat);
    mesh.userData.id = f.id;
    state.routes.add(mesh);
    if (sel) {
      const pin = (p, color) => {
        const m = new THREE.Mesh(new THREE.ConeGeometry(span / 200, span / 60, 12), new THREE.MeshBasicMaterial({ color }));
        m.position.copy(p).add(new THREE.Vector3(0, span / 120, 0)); m.rotation.x = Math.PI;
        state.markers.add(m);
      };
      pin(pts[0], 0x7ddc7d); pin(pts[pts.length - 1], 0xff7b6b);
      state.selectedCurve = curve;
    }
  }
  if (!rs.features.find((f) => f.id === rs.selected)) state.selectedCurve = null;
  $('#v3-fly').disabled = !state.selectedCurve;
  requestRender();
}
nav.routesState.listeners.push(() => { if (state.active) drawRoutes(); });

const rover = new THREE.Mesh(new THREE.BoxGeometry(1, 0.6, 1.4), new THREE.MeshStandardMaterial({ color: 0xf2f2f2 }));
rover.castShadow = true;
function stopFly() {
  if (!state.fly) return;
  cancelAnimationFrame(state.fly.raf);
  state.fly = null;
  scene.remove(rover);
  controls.enabled = true;
  $('#v3-fly').textContent = '▶ Fly along route';
  requestRender();
}
function startFly() {
  if (!state.selectedCurve) return;
  const curve = state.selectedCurve, span = Math.max(state.terrain.width_m, state.terrain.height_m) / 1000;
  const scale = span / 250;
  rover.scale.set(scale, scale, scale);
  scene.add(rover);
  controls.enabled = false;
  $('#v3-fly').textContent = '■ Stop';
  const duration = 25000, t0 = performance.now();
  const step = () => {
    const u = Math.min(1, (performance.now() - t0) / duration);
    const p = curve.getPointAt(u), ahead = curve.getPointAt(Math.min(1, u + 0.01));
    rover.position.copy(p).add(new THREE.Vector3(0, scale * 0.3, 0));
    rover.lookAt(ahead.x, rover.position.y, ahead.z);
    const back = p.clone().sub(ahead).setY(0).normalize().multiplyScalar(span * 0.06);
    camera.position.copy(p).add(back).add(new THREE.Vector3(0, span * 0.03, 0));
    camera.lookAt(ahead);
    renderer.render(scene, camera);
    if (u < 1) state.fly.raf = requestAnimationFrame(step); else stopFly();
  };
  state.fly = { raf: requestAnimationFrame(step) };
}
$('#v3-fly').onclick = () => (state.fly ? stopFly() : startFly());

// ------------------------------------------------------------------ live rover from the ROS 2 bridge
const liveRover = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1.6, 4), new THREE.MeshBasicMaterial({ color: 0x4cc9f0 }));
liveRover.rotation.x = Math.PI;
let liveTrack = null;
nav.rover.listeners.push((st, track) => {
  if (!state.active || !state.terrain) return;
  const [s, w, n, e] = state.terrain.bounds;
  scene.remove(liveRover);
  if (liveTrack) { scene.remove(liveTrack); liveTrack.geometry.dispose(); liveTrack = null; }
  if (!st || st.lat < s || st.lat > n || st.lon < w || st.lon > e) { requestRender(); return; }
  const span = Math.max(state.terrain.width_m, state.terrain.height_m) / 1000;
  const [x, z] = project(st.lat, st.lon);
  liveRover.scale.setScalar(span / 80);
  liveRover.position.set(x, yOf(heightAt(st.lat, st.lon)) + span / 60, z);
  scene.add(liveRover);
  const pts = track.filter(([la, lo]) => la >= s && la <= n && lo >= w && lo <= e)
    .map(([la, lo]) => { const [tx, tz] = project(la, lo); return new THREE.Vector3(tx, yOf(heightAt(la, lo)) + span / 500, tz); });
  if (pts.length > 1) {
    liveTrack = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x4cc9f0 }));
    scene.add(liveTrack);
  }
  requestRender();
});

// ------------------------------------------------------------------ cursor readout + route picking
const ray = new THREE.Raycaster();
let lastMove = 0;
function pick(ev, objects) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), camera);
  return ray.intersectObjects(objects, false)[0];
}
renderer.domElement.addEventListener('pointermove', (ev) => {
  if (!state.mesh || state.fly || performance.now() - lastMove < 80) return;
  lastMove = performance.now();
  const hit = pick(ev, [state.mesh]);
  if (!hit) return;
  const t = state.terrain, [s, w, n, e] = t.bounds;
  const lon = w + (hit.point.x / (t.width_m / 1000) + 0.5) * (e - w);
  const lat = n - (hit.point.z / (t.height_m / 1000) + 0.5) * (n - s);
  $('#cursor').textContent = `${lat.toFixed(4)}°, ${lon.toFixed(4)}°E · elev ${Math.round(heightAt(lat, lon))} m (3D, ×${state.exag})`;
});
renderer.domElement.addEventListener('dblclick', (ev) => {
  const hit = pick(ev, state.routes.children);
  if (hit) nav.selectRoute(hit.object.userData.id);
});

// ------------------------------------------------------------------ HUD wiring
$('#v3-exag').addEventListener('input', () => {
  state.exag = +$('#v3-exag').value; state.userExag = true;
  $('#v3-exag-v').textContent = state.exag;
  if (!state.mesh) return;
  applyHeights(); drawRoutes(); requestRender();
});
$('#v3-reload').onclick = () => load().catch((e) => { $('#v3-status').textContent = e.message; });
$('#v3-res').onchange = $('#v3-reload').onclick;
$('#btn3d').onclick = () => {
  state.active = !state.active;
  el.hidden = !state.active;
  $('#btn3d').textContent = state.active ? '2D' : '3D';
  $('#btn3d').classList.toggle('armed', state.active);
  if (state.active) { resize(); load().catch((e) => { $('#v3-status').textContent = e.message; }); }
  else { stopFly(); }
};

// Debug/automation hook (read-only use: tests, screenshots).
window.autonav3d = { renderer, scene, camera, state, render: () => renderer.render(scene, camera) };
