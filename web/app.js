// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
/* AutoNav-0 2D client — talks only to the local autonav0d service. */
(() => {
'use strict';
const $ = (s) => document.querySelector(s);
const api = (p, opt) => fetch(p, opt).then(async (r) => {
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
});
const MARS_R = 3396190, EARTH_R = 6371000;
// Leaflet's L.Circle assumes Earth's radius for geographic CRSs; scale metres accordingly.
const marsCircle = (ll, m, o) => L.circle(ll, Object.assign({ radius: m * EARTH_R / MARS_R }, o));
const fmtKm = (m) => (m >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m');
const hhmm = (h) => { const m = Math.floor(((h % 24) + 24) % 24 * 60); return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Mars equirectangular CRS matching the NASA Trek EQ WMTS grid (z0 = 2x1 tiles of 256 px).
const crs = L.extend({}, L.CRS.EPSG4326, { R: MARS_R });
const map = L.map('map', { crs, center: [-4.6, 137.4], zoom: 3, minZoom: 0, maxZoom: 16,
  worldCopyJump: false, preferCanvas: true, doubleClickZoom: true });
map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a> · AutoNav-0');

const tileUrl = (l) => `tiles/${l.id}/{z}/{y}/{x}.${l.ext}`;
const makeTrekLayer = (l, opts = {}) => L.tileLayer(tileUrl(l), Object.assign({
  tileSize: 256, maxNativeZoom: l.maxZoom, maxZoom: 16, noWrap: true,
  bounds: l.global ? undefined : L.latLngBounds([l.bbox[1], l.bbox[0]], [l.bbox[3], l.bbox[2]]),
  attribution: `<a href="https://trek.nasa.gov/mars/">NASA Mars Trek</a>: ${esc(l.title)}`,
}, opts));

let LAYERS = [], basemap = null;
let HIST = [];                 // historic rover routes (GeoJSON features)
const histOn = new Map();      // id -> Leaflet layer
const active = new Map(); // id -> {layer, meta}

// ---------------------------------------------------------------- basemap + Trek catalog
api('api/layers').then((layers) => {
  LAYERS = layers;
  $('#layer-count').textContent = `(${layers.length})`;
  const sel = $('#basemap');
  const globals = layers.filter((l) => l.global && ['Imagery', 'Topography', 'Terrain', 'ThermalInertia', 'Geology'].includes(l.category));
  for (const l of globals) sel.add(new Option(l.title, l.id));
  sel.value = globals.find((l) => l.id === 'Mars_Viking_MDIM21_ClrMosaic_global_232m') ? 'Mars_Viking_MDIM21_ClrMosaic_global_232m' : globals[0]?.id;
  sel.onchange = () => setBasemap(sel.value);
  setBasemap(sel.value);
  renderLayerList();
}).catch((e) => { $('#layer-list').innerHTML = `<div class="err">${esc(e.message)}</div>`; });

function setBasemap(id) {
  const l = LAYERS.find((x) => x.id === id);
  if (!l) return;
  if (basemap) map.removeLayer(basemap);
  basemap = makeTrekLayer(l, { zIndex: 1 }).addTo(map);
}

function inView(l) {
  if (l.global) return true;
  const b = map.getBounds();
  return !(l.bbox[2] < b.getWest() || l.bbox[0] > b.getEast() || l.bbox[3] < b.getSouth() || l.bbox[1] > b.getNorth());
}

function renderLayerList() {
  const q = $('#layer-filter').value.toLowerCase();
  const onlyView = $('#layer-inview').checked;
  const list = LAYERS.filter((l) => (!q || (l.title + l.instrument + l.mission + l.category).toLowerCase().includes(q)) && (!onlyView || inView(l)));
  const byCat = {};
  for (const l of list) (byCat[l.category] ||= []).push(l);
  let html = '';
  const hist = HIST.filter((f) => (!q || (f.properties.name + ' ' + f.properties.rover + ' historic route traverse').toLowerCase().includes(q)) &&
    (!onlyView || inView({ global: false, bbox: f.bbox })));
  if (hist.length) {
    html += `<div class="cat">Historic rover routes (${hist.length})</div>`;
    for (const f of hist) {
      const p = f.properties;
      html += `<div class="layer"><div class="t">${esc(p.name)}<small>${esc(p.mission)} · ${fmtKm(p.distance_m)}${p.kind === 'endpoints' ? ' · endpoints only' : ''}</small></div>
        <button data-hzoom="${esc(p.id)}" title="Zoom to">⌖</button>
        <button data-hplan="${esc(p.id)}" title="Load start/end into the route planner and compare">Plan</button>
        <button data-hadd="${esc(p.id)}" title="Show on map">${histOn.has(p.id) ? '✓' : '+'}</button></div>`;
    }
  }
  for (const [cat, ls] of Object.entries(byCat).sort()) {
    html += `<div class="cat">${esc(cat)} (${ls.length})</div>`;
    for (const l of ls) {
      html += `<div class="layer"><div class="t">${esc(l.title)}<small>${esc([l.mission, l.instrument].filter(Boolean).join(' · '))}${l.global ? ' · global' : ''} · z≤${l.maxZoom}</small></div>
        ${l.global ? '' : `<button data-zoom="${esc(l.id)}" title="Zoom to">⌖</button>`}
        <button data-add="${esc(l.id)}" title="Add overlay">${active.has(l.id) ? '✓' : '+'}</button></div>`;
    }
  }
  $('#layer-list').innerHTML = html || '<div class="hint" style="padding:6px">No layers in view — zoom out or untick “only in view”.</div>';
}
$('#layer-filter').oninput = renderLayerList;
$('#layer-inview').onchange = renderLayerList;
map.on('moveend', () => { if ($('#layer-inview').checked) renderLayerList(); });
$('#layer-list').onclick = (e) => {
  const { hadd, hplan, hzoom } = e.target.dataset;
  if (hadd) toggleHistoric(hadd);
  if (hzoom) zoomHistoric(hzoom);
  if (hplan) planHistoric(hplan);
  const add = e.target.dataset.add, zoom = e.target.dataset.zoom;
  if (add) toggleOverlay(add);
  if (zoom) { const l = LAYERS.find((x) => x.id === zoom); map.fitBounds([[l.bbox[1], l.bbox[0]], [l.bbox[3], l.bbox[2]]]); }
};

// ---------------------------------------------------------------- historic rover routes
api('api/historic').then((fc) => {
  HIST = fc.features;
  for (const f of HIST) {
    const c = f.geometry.coordinates;
    f.bbox = [Math.min(...c.map((p) => p[0])), Math.min(...c.map((p) => p[1])), Math.max(...c.map((p) => p[0])), Math.max(...c.map((p) => p[1]))];
  }
  renderLayerList();
}).catch(() => {});
const histById = (id) => HIST.find((f) => f.id === id);
function toggleHistoric(id, on) {
  const f = histById(id);
  if (!f) return;
  if (histOn.has(id) && on !== true) { map.removeLayer(histOn.get(id)); histOn.delete(id); }
  else if (!histOn.has(id)) {
    const p = f.properties, endpointsOnly = p.kind === 'endpoints';
    const g = L.layerGroup();
    const ll = f.geometry.coordinates.map((c) => [c[1], c[0]]);
    L.polyline(ll, { color: '#000', weight: 5, opacity: 0.4, interactive: false }).addTo(g);
    L.polyline(ll, { color: '#ffffff', weight: 2.5, dashArray: endpointsOnly ? '2 8' : '8 5' })
      .bindTooltip(`${esc(p.name)} · ${fmtKm(p.distance_m)}${endpointsOnly ? ' (endpoints only)' : ''}`, { sticky: true }).addTo(g);
    const end = (pt, color, label) => L.circleMarker([pt.lat, pt.lon], { radius: 6, color: '#fff', weight: 2, fillColor: color, fillOpacity: 1 })
      .bindTooltip(`${label}: ${esc(pt.label)}`).addTo(g);
    end(p.start, '#7ddc7d', 'Start'); end(p.end, '#ff7b6b', 'End');
    histOn.set(id, g.addTo(map));
  }
  renderLayerList();
}
function zoomHistoric(id) {
  const f = histById(id);
  if (!f) return;
  map.fitBounds([[f.bbox[1], f.bbox[0]], [f.bbox[3], f.bbox[2]]], { padding: [40, 40], maxZoom: 15 });
  // Like Trek bookmarks: switch on the sharpest local mosaic (HiRISE, else CTX) covering the route.
  const area = (l) => (l.bbox[2] - l.bbox[0]) * (l.bbox[3] - l.bbox[1]);
  const covers = (l) => !l.global && l.bbox[2] - l.bbox[0] < 60 && l.category === 'Imagery' &&
    l.bbox[0] <= f.bbox[0] && l.bbox[1] <= f.bbox[1] && l.bbox[2] >= f.bbox[2] && l.bbox[3] >= f.bbox[3];
  const pick = ['HiRISE', 'CTX'].map((inst) => LAYERS.filter((l) => covers(l) && l.instrument === inst)
    .sort((a, b) => area(a) - area(b))[0]).find(Boolean);
  if (pick) toggleOverlay(pick.id, true);
}
function planHistoric(id) {
  const f = histById(id);
  if (!f) return;
  const p = f.properties;
  toggleHistoric(id, true);
  zoomHistoric(id);
  setPoint('start', L.latLng(p.start.lat, p.start.lon));
  setPoint('goal', L.latLng(p.end.lat, p.end.lon));
  // Score what the rover actually drove next to the planned routes (not for endpoint-only routes).
  route.ref = p.kind === 'endpoints' ? null : id;
  route.refLabel = p.name;
  document.querySelector('.tabs button[data-tab=navigation]').click();
  $('#route-go').click();
}

function toggleOverlay(id, on) {
  const l = LAYERS.find((x) => x.id === id);
  if (!l) return;
  if (active.has(id) && on !== true) { map.removeLayer(active.get(id).layer); active.delete(id); }
  else if (!active.has(id)) active.set(id, { layer: makeTrekLayer(l, { zIndex: 10 + active.size, opacity: 1 }).addTo(map), meta: l });
  renderActive(); renderLayerList();
}
function renderActive() {
  if (!active.size) { $('#active-list').innerHTML = '<em>none</em>'; return; }
  $('#active-list').innerHTML = [...active.values()].map(({ meta, layer }) =>
    `<div class="layer"><div class="t">${esc(meta.title)}</div>
     <input type="range" min="0" max="100" value="${Math.round(layer.options.opacity * 100)}" data-op="${esc(meta.id)}">
     <button data-rm="${esc(meta.id)}">✕</button></div>`).join('');
}
$('#active-list').oninput = (e) => { const id = e.target.dataset.op; if (id) active.get(id).layer.setOpacity(e.target.value / 100); };
$('#active-list').onclick = (e) => { if (e.target.dataset.rm) toggleOverlay(e.target.dataset.rm); };

// ---------------------------------------------------------------- DEM-derived overlays
const sun = { az: 315, el: 30 };
const demLayer = (kind, extra) => L.tileLayer(`dem/${kind}/{z}/{y}/{x}.png?{q}`, {
  q: extra, tileSize: 256, maxZoom: 16, noWrap: true, zIndex: 50,
  zIndex: kind === 'comms' ? 60 : 50,
  attribution: kind === 'terrain'
    ? 'Terrain classes: DoMars16k (Wilhelm et al. 2020) + DINOv2, MRO CTX via NASA Trek, AutoNav-0'
    : kind === 'comms' ? 'Comm coverage: relay-pass terrain masking from MGS MOLA, AutoNav-0'
    : 'Terrain: MGS MOLA MEGDR (NASA PDS) via autonav0d' });
const demOv = { hillshade: null, slope: null, contours: null, relief: null, terrain: null, comms: null };
const maxSlope = () => +$('#max-slope').value;
const demQuery = (k) => k === 'hillshade' ? `az=${sun.az}&el=${sun.el}` : k === 'slope' ? `max_slope=${maxSlope()}` : '';
function refreshDem(kind) {
  const on = $('#ov-' + kind).checked;
  if (demOv[kind]) { map.removeLayer(demOv[kind]); demOv[kind] = null; }
  if (on) demOv[kind] = demLayer(kind, demQuery(kind)).addTo(map);
}
['hillshade', 'slope', 'contours', 'relief', 'terrain', 'comms'].forEach((k) => { $('#ov-' + k).onchange = () => refreshDem(k); });
refreshDem('comms');  // on by default: dead spots must be visible while planning

// Terrain-class legends + mapped-region list from /api/terrain/classes.
const legendHtml = (classes) => {
  const groups = new Map();
  Object.entries(classes).sort((a, b) => a[0] - b[0]).forEach(([, c]) => {
    const g = c.group || '';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  });
  return [...groups].map(([g, cs]) => `${g ? `<div class="lg-group">${esc(g)}</div>` : ''}` +
    cs.map((c) => `<span class="lg"><i style="background:${c.color}"></i>${esc(c.name)}</span>`).join('')).join('');
};
api('api/terrain/classes').then((t) => {
  if (t.comms && t.comms.length) {
    const visible = Object.fromEntries(Object.entries(t.comms[0].classes).filter(([, c]) => c.color !== 'none'));
    $('#legend-comms').innerHTML = legendHtml(visible).replace(/<div class="lg-group">[^<]*<\/div>/, '');
  }
  if (t.relief.length) $('#legend-relief').innerHTML = legendHtml(t.relief[0].classes);
  if (t.terrain.length) {
    $('#legend-terrain').innerHTML = legendHtml(t.terrain[0].classes);
    const sel = $('#terrain-region');
    t.terrain.slice().sort((a, b) => a.name.localeCompare(b.name)).forEach((g) => {
      const o = document.createElement('option');
      o.value = JSON.stringify([[g.lat_min, g.lon_min], [g.lat_max, g.lon_max]]);
      o.textContent = g.name;
      sel.appendChild(o);
    });
    $('#terrain-note').textContent = `${t.terrain.length} mapped grids. ~87–89% accuracy on the DoMars16k test set; ` +
      'grey = uncertain. Classes adjust rover cost (dunes = soft, rough/cliffs = rocky).';
  } else {
    $('#terrain-note').textContent = 'No terrain-class grids yet: run scripts/terrain_map.py (GPU, once).';
  }
}).catch(() => {});
$('#terrain-region').onchange = (e) => {
  if (!e.target.value) return;
  map.fitBounds(JSON.parse(e.target.value));
  if (!$('#ov-terrain').checked) { $('#ov-terrain').checked = true; refreshDem('terrain'); syncLegends(); }
  e.target.value = '';
};
const syncLegends = () => {
  $('#legend-comms').hidden = !$('#ov-comms').checked;
  $('#legend-relief').hidden = !$('#ov-relief').checked;
  $('#terrain-sub').hidden = !$('#ov-terrain').checked;
};
['relief', 'terrain', 'comms'].forEach((k) => $('#ov-' + k).addEventListener('change', syncLegends));
let sunTimer;
function setSun(az, el, fromSlider) {
  sun.az = Math.round(az); sun.el = Math.round(el);
  $('#sun-az-v').textContent = sun.az; $('#sun-el-v').textContent = sun.el;
  if (!fromSlider) { $('#sun-az').value = sun.az; $('#sun-el').value = sun.el; }
  clearTimeout(sunTimer); sunTimer = setTimeout(() => refreshDem('hillshade'), 250);
}
$('#sun-az').oninput = () => setSun(+$('#sun-az').value, +$('#sun-el').value, true);
$('#sun-el').oninput = () => setSun(+$('#sun-az').value, +$('#sun-el').value, true);
$('#sun-now').onclick = () => {
  const c = map.getCenter();
  api(`api/sun?lat=${c.lat}&lon=${c.lng}`).then((s) => { setSun(s.sun_azimuth_deg, s.sun_elevation_deg); $('#ov-hillshade').checked = true; refreshDem('hillshade'); });
};
setSun(315, 30);
$('#max-slope').oninput = () => {
  document.querySelectorAll('.slopev').forEach((e) => { e.textContent = maxSlope(); });
  clearTimeout(sunTimer); sunTimer = setTimeout(() => refreshDem('slope'), 300);
};

// ---------------------------------------------------------------- mission vectors
const vec = {};
async function loadVector(name, style) {
  if (vec[name]) return vec[name];
  const gj = await api('api/vector/' + name);
  vec[name] = L.geoJSON(gj, style);
  return vec[name];
}
const travStyle = (color) => ({ style: { color, weight: 2.5, opacity: 0.95 } });
const wpStyle = (color, rover) => ({
  pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 3, color, weight: 1, fillOpacity: 0.8 }),
  onEachFeature: (f, lyr) => { const p = f.properties; lyr.bindPopup(`<b>${rover}</b> sol ${esc(p.sol)}<br>site ${esc(p.site)} drive ${esc(p.drive)}<br>odometry ${esc(p.dist_km ?? '')} km<br>elev ${esc(p.elev_geoid ?? '')} m`); },
});
async function toggleVector(chk, names) {
  for (const [name, style] of names) {
    try {
      const l = await loadVector(name, style);
      $(chk).checked ? l.addTo(map) : map.removeLayer(l);
    } catch (e) { console.warn(name, e); }
  }
}
$('#ov-msl').onchange = () => toggleVector('#ov-msl', [['msl_traverse', travStyle('#ffd166')]]);
$('#ov-m20').onchange = () => toggleVector('#ov-m20', [['m20_traverse', travStyle('#4cc9f0')]]);
$('#ov-wp').onchange = () => toggleVector('#ov-wp', [['msl_waypoints', wpStyle('#ffd166', 'Curiosity')], ['m20_waypoints', wpStyle('#4cc9f0', 'Perseverance')]]);

// ---------------------------------------------------------------- nomenclature + search
let NAMES = [];
const nameLayer = L.layerGroup().addTo(map);
api('api/nomenclature').then((n) => { NAMES = n; drawNames(); });
function drawNames() {
  nameLayer.clearLayers();
  if (!$('#ov-names').checked) return;
  const b = map.getBounds(), z = map.getZoom();
  let n = 0;
  for (const f of NAMES) {
    if (z < 4 && /crater/i.test(f.type)) continue;
    if (!b.contains([f.lat, f.lon])) continue;
    if (++n > 150) break;
    L.marker([f.lat, f.lon], { interactive: false, icon: L.divIcon({ className: 'name-label', html: esc(f.name), iconSize: null }) }).addTo(nameLayer);
  }
}
map.on('moveend', drawNames);
$('#ov-names').onchange = drawNames;

const results = $('#search-results');
$('#search').oninput = () => {
  const q = $('#search').value.trim().toLowerCase();
  if (q.length < 2) { results.style.display = 'none'; return; }
  const hits = NAMES.filter((f) => f.name.toLowerCase().includes(q)).slice(0, 25);
  results.innerHTML = hits.map((f, i) => `<div data-i="${i}">${esc(f.name)} <small>${esc(f.type)} · ${f.lat.toFixed(2)}, ${f.lon.toFixed(2)}</small></div>`).join('') || '<div><small>no match</small></div>';
  results.style.display = 'block';
  results.onclick = (e) => {
    const d = e.target.closest('[data-i]'); if (!d) return;
    const f = hits[+d.dataset.i];
    map.flyTo([f.lat, f.lon], Math.max(map.getZoom(), 7));
    results.style.display = 'none';
  };
};
document.addEventListener('click', (e) => { if (!e.target.closest('#search, #search-results')) results.style.display = 'none'; });

// ---------------------------------------------------------------- bookmarks
api('api/bookmarks').then((bms) => {
  const sel = $('#bookmarks');
  bms.forEach((b, i) => sel.add(new Option(b.name, i)));
  sel.onchange = () => {
    const b = bms[+sel.value]; if (!b) return;
    if (b.bbox) map.flyToBounds([[b.bbox[1], b.bbox[0]], [b.bbox[3], b.bbox[2]]]);
    else map.flyTo([b.lat, b.lon], b.zoom || 8);
    // Like Trek: bookmarks switch on their associated local layers.
    (b.layers || []).forEach((id) => { const l = LAYERS.find((x) => x.id === id); if (l && !l.global) toggleOverlay(id, true); });
    sel.value = '';
  };
});

// ---------------------------------------------------------------- cursor readout & Mars clock
let elevTimer;
map.on('mousemove', (e) => {
  const { lat, lng } = e.latlng;
  const base = `${lat.toFixed(4)}°, ${lng.toFixed(4)}°E`;
  $('#cursor').textContent = base;
  clearTimeout(elevTimer);
  elevTimer = setTimeout(() => api(`api/elevation?lat=${lat}&lon=${lng}`)
    .then((r) => {
      let t = `${base} · elev ${r.elev_m.toFixed(0)} m (MOLA ${r.res_m} m/px)`;
      if (r.relief) t += ` · ${r.relief.name}`;
      if (r.terrain) t += ` › ${r.terrain.group ? r.terrain.group + ': ' : ''}${r.terrain.name}`;
      if (r.comms && r.comms.code > 1) t += ` · ⚠ ${r.comms.name}`;
      $('#cursor').textContent = t;
    })
    .catch(() => {}), 120);
});
map.on('zoomend', () => { $('#zoomv').textContent = 'z' + map.getZoom(); });
$('#zoomv').textContent = 'z' + map.getZoom();
function tickClock() {
  const c = map.getCenter();
  api(`api/sun?lat=${c.lat}&lon=${c.lng}`).then((s) => {
    $('#marstime').textContent = `MSD ${Math.floor(s.msd)} · LTST ${hhmm(s.ltst_h)} · Ls ${s.ls_deg.toFixed(1)}° · Sun ${s.sun_elevation_deg.toFixed(0)}° el / ${s.sun_azimuth_deg.toFixed(0)}° az`;
  }).catch(() => {});
}
tickClock(); setInterval(tickClock, 10000); map.on('moveend', tickClock);

// ---------------------------------------------------------------- online / offline (orbiter uplink)
function health() {
  return api('api/health').then((h) => {
    const b = $('#netmode');
    b.textContent = h.online ? '📡 Uplink: online' : '📴 Offline';
    b.className = h.online ? 'on' : 'off';
    b.dataset.online = h.online ? '1' : '0';
    const rl = $('#rerun-link');
    if (h.gateway_url) {
      const gw = h.gateway_url.replaceAll('{host}', location.hostname).replace(/\/$/, '');
      $('#exit-app').hidden = false;
      $('#exit-app').onclick = () => {
        if (confirm('Exit AutoNav-0? The navigation computer goes to sleep; the entry screen stays on.'))
          location.href = gw + '/?exit=1';
      };
    }
    if (h.rerun_url) { rl.href = h.rerun_url.replaceAll('{host}', location.hostname); rl.hidden = false; } else rl.hidden = true;
    $('#health').innerHTML = `<h3>Service</h3><code>autonav0d ${esc(h.version)}</code> · up ${h.uptime_s}s<br>
      tiles: ${h.tiles.hits} cached, ${h.tiles.fetched} fetched, ${h.tiles.misses} misses<br>
      DEMs: ${h.dems.map((d) => `${esc(d.name)} (${d.res_m} m/px)`).join('<br>')}`;
  }).catch(() => { $('#netmode').textContent = '⚠ service down'; });
}
$('#netmode').onclick = () => {
  const off = $('#netmode').dataset.online === '1' ? 1 : 0;
  api('api/mode?offline=' + off, { method: 'POST' }).then(health).catch((e) => alert(e.message));
};
health(); setInterval(health, 15000);

// ---------------------------------------------------------------- tabs
document.querySelectorAll('.tabs button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('.tabs button, .tab').forEach((x) => x.classList.remove('active'));
  b.classList.add('active'); $('#tab-' + b.dataset.tab).classList.add('active');
});

// ---------------------------------------------------------------- route planner
const route = { start: null, goal: null, layer: L.layerGroup().addTo(map), pick: null };
const pinIcon = (c, t) => L.divIcon({ className: '', html: `<div style="background:${c};color:#000;border-radius:50%;width:18px;height:18px;text-align:center;font:bold 11px/18px sans-serif;border:2px solid #fff">${t}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] });
let startM = null, goalM = null;
function arm(which) {
  route.pick = route.pick === which ? null : which;
  $('#pick-start').classList.toggle('armed', route.pick === 'start');
  $('#pick-goal').classList.toggle('armed', route.pick === 'goal');
  map.getContainer().style.cursor = route.pick ? 'crosshair' : '';
}
$('#pick-start').onclick = () => arm('start');
$('#pick-goal').onclick = () => arm('goal');
function setPoint(which, ll) {
  route[which] = ll;
  route.ref = null;  // a manual start/goal no longer matches a historic traverse
  const m = L.marker(ll, { draggable: true, icon: pinIcon(which === 'start' ? '#7ddc7d' : '#ff7b6b', which === 'start' ? 'A' : 'B') })
    .on('dragend', (e) => { route[which] = e.target.getLatLng(); route.ref = null; });
  if (which === 'start') { if (startM) map.removeLayer(startM); startM = m.addTo(map); }
  else { if (goalM) map.removeLayer(goalM); goalM = m.addTo(map); }
}
const PROFILE_COLORS = { safe: '#7ddc7d', wear: '#ff9f1c', eva: '#c77dff', shortest: '#ffd166' };
const BEST_LABELS = { shortest_distance: 'Shortest', fastest_eva: 'Fastest EVA', least_wear: 'Least wear',
  least_energy: 'Least energy', gentlest: 'Gentlest' };
const routeColor = (p) => (p.reference ? '#ffffff' : PROFILE_COLORS[p.profile] || '#ccc');
// Shared state (also read by the 3D view).
const routesState = { features: [], best: {}, selected: null, listeners: [] };
const notifyRoutes = () => routesState.listeners.forEach((f) => f(routesState));

$('#route-clear').onclick = () => {
  route.layer.clearLayers(); route.start = route.goal = null;
  [startM, goalM].forEach((m) => m && map.removeLayer(m)); startM = goalM = null;
  routesState.features = []; routesState.selected = null; notifyRoutes();
  $('#route-out').innerHTML = ''; $('#route-cards').innerHTML = ''; clearChart();
};

function drawRoutes() {
  route.layer.clearLayers();
  const sel = routesState.features.find((f) => f.id === routesState.selected);
  // Unselected first (thin, muted), selected last so it sits on top — like map apps.
  for (const f of routesState.features) {
    if (f === sel) continue;
    const coords = f.geometry.coordinates.map((c) => [c[1], c[0]]);
    L.polyline(coords, { color: '#000', weight: 6, opacity: 0.35 }).addTo(route.layer);
    L.polyline(coords, { color: routeColor(f.properties), weight: 4, opacity: 0.55,
      dashArray: f.properties.alternative || f.properties.reference ? '6 6' : null })
      .on('click', () => selectRoute(f.id)).bindTooltip(f.properties.label, { sticky: true }).addTo(route.layer);
  }
  if (!sel) return;
  const p = sel.properties, coords = sel.geometry.coordinates.map((c) => [c[1], c[0]]);
  // Dead-reckoning uncertainty: 1-sigma circles along the selected route.
  const step = Math.max(1, Math.floor(p.samples.length / 25));
  p.samples.forEach((s, i) => { if (i % step === 0 && s.sigma > 0) marsCircle(coords[i], s.sigma, { color: '#4cc9f0', weight: 1, opacity: 0.5, fillOpacity: 0.06, interactive: false }).addTo(route.layer); });
  L.polyline(coords, { color: '#000', weight: 8, opacity: 0.6 }).addTo(route.layer);
  L.polyline(coords, { color: routeColor(p), weight: 5, dashArray: p.alternative || p.reference ? '10 6' : null }).addTo(route.layer);
  p.reloc_stops.forEach((s, i) => L.circleMarker([s.lat, s.lon], { radius: 4, color: '#4cc9f0', weight: 2, fillColor: '#0b3d4f', fillOpacity: 1 })
    .bindTooltip(`Re-localise #${i + 1} @ ${fmtKm(s.dist_m)}`).addTo(route.layer));
}

function renderCards() {
  const badges = {};
  for (const [k, id] of Object.entries(routesState.best)) (badges[id] ||= []).push(BEST_LABELS[k] || k);
  $('#route-cards').innerHTML = routesState.features.map((f) => {
    const p = f.properties;
    return `<div class="rcard${f.id === routesState.selected ? ' sel' : ''}" data-id="${esc(f.id)}">
      <div class="rhead"><span class="swatch" style="background:${routeColor(p)}${p.alternative ? ';opacity:.6' : ''}"></span>
        <b>${esc(p.label)}</b></div>
      <div class="rmain">${fmtKm(p.distance_m)} · ${p.eva_hours} h EVA · ${p.rover_sols} sols</div>
      <div class="rsub">wear ${p.wear_index} · max ${p.max_slope_deg}° · ↑${p.ascent_m} m${p.comm_dead_km > 0 ? ` · <span class="warn">${p.comm_dead_km} km no comms</span>` : ''}${p.overlap_with_primary < 1 ? ` · ${Math.round(p.overlap_with_primary * 100)}% shared` : ''}</div>
      ${p.reference ? '<span class="badge hist">Actually driven</span>' : ''}${(badges[f.id] || []).map((b) => `<span class="badge">${esc(b)}</span>`).join('')}
    </div>`;
  }).join('');
}
$('#route-cards').onclick = (e) => { const c = e.target.closest('.rcard'); if (c) selectRoute(c.dataset.id); };

// Relative difference vs the fastest/least-wear choice, e.g. "+1.3 km, −9 % wear".
const delta = (v, ref, unit, digits = 1) => {
  const d = v - ref;
  if (Math.abs(d) < 1e-9) return '';
  return ` <small>(${d > 0 ? '+' : '−'}${Math.abs(d).toFixed(digits)}${unit})</small>`;
};

function selectRoute(id) {
  routesState.selected = id;
  const f = routesState.features.find((x) => x.id === id);
  if (!f) return;
  const p = f.properties;
  const ref = routesState.features[0].properties;  // primary route
  drawRoutes(); renderCards(); notifyRoutes();
  $('#route-out').innerHTML = `<table class="tel">
    <tr><td>Route</td><td>${esc(p.label)} (limit ${p.max_slope_limit_deg}°)</td></tr>
    <tr><td>Distance (2D / 3D)</td><td>${fmtKm(p.distance_m)} / ${fmtKm(p.distance_3d_m)}${delta(p.distance_m / 1000, ref.distance_m / 1000, ' km', 2)}</td></tr>
    <tr><td>Ascent / descent</td><td>${p.ascent_m} m / ${p.descent_m} m</td></tr>
    <tr><td>Max / mean terrain slope</td><td>${p.max_slope_deg}° / ${p.mean_slope_deg}°</td></tr>
    <tr><td>EVA walking time</td><td>${p.eva_hours} h${delta(p.eva_hours, ref.eva_hours, ' h', 2)}</td></tr>
    <tr><td>Rover drive</td><td>${p.rover_sols} sols · ${(p.rover_energy_wh / 1000).toFixed(1)} kWh</td></tr>
    <tr><td title="Wear-equivalent km: 1 = 1 km of flat, smooth, benign driving">Wheel wear index</td><td>${p.wear_index}${delta(p.wear_index, ref.wear_index, '', 2)}</td></tr>
    <tr><td>Comm dead / poor coverage</td><td>${p.comm_coverage ? `${p.comm_dead_km} / ${p.comm_poor_km} km` : 'n/a'}</td></tr>
    <tr><td>Rough / rocky / soft ground</td><td>${p.rough_km} / ${p.thermal_inertia || p.terrain_classes ? p.rock_km : 'n/a'} / ${p.thermal_inertia || p.terrain_classes ? p.soft_km : 'n/a'} km${p.terrain_classes ? ' <small>(mapped terrain classes)</small>' : ''}</td></tr>
    <tr><td>Heading changes</td><td>${p.turn_deg}°</td></tr>
    <tr><td>Re-localisation stops</td><td>${p.reloc_stops.length}</td></tr>
    <tr><td>DEM / grid</td><td>${p.dem_resolution_m} m · ${p.grid[0]}×${p.grid[1]}</td></tr></table>
    ${p.reference ? `<div class="hint">Historic traverse resampled onto the ${p.grid_cell_m} m planning grid so every route is scored
      with the same model; the logged odometry was ${fmtKm(histById(p.id.replace('historic:', ''))?.properties.distance_m || p.distance_m)}.
      Detours the rover made around hazards smaller than the DEM pixel show up as extra distance.</div>` : ''}
    <div class="hint">Deltas vs the primary route. Wear model: slope load, DEM roughness, TES thermal
      inertia (rocky &gt;400 / soft &lt;150) and turning scrub.</div>`;
  drawChart(p.samples.map((s) => [s.d, s.elev, s.slope]), maxSlope(),
    routesState.features.filter((x) => x !== f).map((x) => x.properties.samples.map((s) => [s.d, s.elev])));
}

$('#route-go').onclick = async () => {
  if (!route.start || !route.goal) { $('#route-out').innerHTML = '<div class="err">Set a start and a goal first.</div>'; return; }
  const primary = $('#route-profile').value;
  const profiles = [primary, ...($('#route-compare').checked ? ['safe', 'wear', 'eva', 'shortest'].filter((p) => p !== primary) : [])];
  const q = new URLSearchParams({
    from: `${route.start.lat},${route.start.lng}`, to: `${route.goal.lat},${route.goal.lng}`,
    profiles: profiles.join(','), alternatives: $('#route-alts').value, max_slope: maxSlope(),
    drift: +$('#drift').value / 100, reloc_sigma: +$('#reloc').value, comm_weight: $('#comm-weight').value });
  if (route.ref) q.set('ref', route.ref);
  $('#route-out').innerHTML = '<div class="hint">planning…</div>';
  $('#route-cards').innerHTML = '';
  try {
    const fc = await api('api/routes?' + q);
    // Hide profile routes that are effectively identical to an earlier one (keeps the list useful)
    // and move their "best" badges to the route that stays.
    const kept = [], alias = {};
    for (const f of fc.features) {
      const p = f.properties;
      const dup = kept.find((k) => Math.abs(k.properties.distance_m - p.distance_m) < 1 &&
        Math.abs(k.properties.wear_index - p.wear_index) < 0.01);
      if (dup && !p.reference && !dup.properties.reference) { dup.properties.label += ` = ${p.label}`; alias[f.id] = dup.id; } else kept.push(f);
    }
    routesState.features = kept;
    routesState.best = Object.fromEntries(Object.entries(fc.best).map(([k, id]) => [k, alias[id] || id]));
    selectRoute(kept[0].id);
    if (fc.failed.length) $('#route-out').insertAdjacentHTML('beforeend', fc.failed.map((x) => `<div class="err">${esc(x.label)}: ${esc(x.error)}</div>`).join(''));
    $('#route-out').insertAdjacentHTML('beforeend', `<div class="hint">${kept.length} routes in ${fc.compute_ms} ms</div>`);
  } catch (e) { $('#route-out').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};

// ---------------------------------------------------------------- elevation profile tool
const prof = { drawing: false, pts: [], layer: L.layerGroup().addTo(map) };
$('#prof-draw').onclick = () => {
  prof.drawing = !prof.drawing; prof.pts = []; prof.layer.clearLayers();
  $('#prof-draw').classList.toggle('armed', prof.drawing);
  map.getContainer().style.cursor = prof.drawing ? 'crosshair' : '';
  if (prof.drawing) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
};
$('#prof-clear').onclick = () => { prof.pts = []; prof.layer.clearLayers(); $('#prof-out').innerHTML = ''; clearChart(); };
map.on('click', (e) => {
  if (route.pick) { setPoint(route.pick, e.latlng); arm(route.pick); return; }
  if (!prof.drawing) return;
  prof.pts.push(e.latlng);
  prof.layer.clearLayers();
  L.polyline(prof.pts, { color: '#ffd166', weight: 2, dashArray: '4 4' }).addTo(prof.layer);
});
map.on('dblclick', async () => {
  if (!prof.drawing || prof.pts.length < 2) return;
  $('#prof-draw').click();
  const pts = prof.pts.map((p) => `${p.lat},${p.lng}`).join(';');
  try {
    const r = await api(`api/profile?n=256&pts=${encodeURIComponent(pts)}`);
    const el = r.samples.map((s) => s.elev).filter((v) => v !== null);
    $('#prof-out').innerHTML = `<table class="tel"><tr><td>Length</td><td>${fmtKm(r.distance_m)}</td></tr>
      <tr><td>Elevation min / max</td><td>${Math.min(...el)} / ${Math.max(...el)} m</td></tr>
      <tr><td>Max sampled slope</td><td>${r.max_slope_deg}°</td></tr></table>`;
    drawChart(r.samples.map((s) => [s.d, s.elev, s.slope]), maxSlope());
  } catch (e) { $('#prof-out').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
});

// ---------------------------------------------------------------- tiny canvas chart
function clearChart() { const c = $('#chart'); c.getContext('2d').clearRect(0, 0, c.width, c.height); }
function drawChart(rows, slopeLimit, others = []) {
  const c = $('#chart'), ctx = c.getContext('2d');
  c.width = c.clientWidth * devicePixelRatio; c.height = c.clientHeight * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const W = c.clientWidth, H = c.clientHeight, pad = 30;
  ctx.clearRect(0, 0, W, H);
  rows = rows.filter((r) => r[1] !== null);
  if (rows.length < 2) return;
  const all = [rows, ...others].flat().filter((r) => r[1] !== null);
  const dmax = Math.max(...all.map((r) => r[0])) || 1;
  const emin = Math.min(...all.map((r) => r[1])), emax = Math.max(...all.map((r) => r[1])) + 1;
  const X = (d) => pad + (W - pad - 6) * d / dmax, Y = (e) => H - 16 - (H - 26) * (e - emin) / (emax - emin);
  ctx.strokeStyle = 'rgba(200,190,180,.35)'; ctx.lineWidth = 1;
  for (const o of others) {
    ctx.beginPath();
    o.forEach((r, i) => (i ? ctx.lineTo(X(r[0]), Y(r[1])) : ctx.moveTo(X(r[0]), Y(r[1]))));
    ctx.stroke();
  }
  for (let i = 1; i < rows.length; i++) {
    const s = rows[i][2];
    ctx.strokeStyle = s >= slopeLimit ? '#ff4d3d' : s >= slopeLimit * 0.6 ? '#ffb347' : '#7ddc7d';
    ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(X(rows[i - 1][0]), Y(rows[i - 1][1])); ctx.lineTo(X(rows[i][0]), Y(rows[i][1])); ctx.stroke();
  }
  ctx.fillStyle = '#a8968a'; ctx.font = '10px sans-serif';
  ctx.fillText(`${Math.round(emax)} m`, 2, 12); ctx.fillText(`${Math.round(emin)} m`, 2, H - 18);
  ctx.fillText('0', pad, H - 4); ctx.fillText(fmtKm(dmax), W - 50, H - 4);
}
// ---------------------------------------------------------------- live rover (ROS 2 bridge -> autonav0d)
const rover = { layer: L.layerGroup().addTo(map), listeners: [], last: null, timer: null };
const roverIcon = (hdg) => L.divIcon({ className: '', iconSize: [26, 26], iconAnchor: [13, 13],
  html: `<div style="transform:rotate(${hdg || 0}deg);width:26px;height:26px;display:flex;align-items:center;justify-content:center">
    <svg width="22" height="22" viewBox="0 0 22 22"><path d="M11 1 L19 20 L11 15 L3 20 Z" fill="#4cc9f0" stroke="#fff" stroke-width="1.5"/></svg></div>` });
async function pollRover() {
  let next = 3000;
  try {
    const r = await api('api/rover/state');
    rover.layer.clearLayers();
    const st = r.state;
    if (!st || !$('#ov-rover').checked) { $('#rover-info').textContent = st ? 'hidden' : 'no rover connected'; rover.last = st; }
    else {
      next = 1000;
      const ll = [st.lat, st.lon];
      if (r.track?.length > 1) L.polyline(r.track, { color: '#4cc9f0', weight: 2, opacity: 0.8 }).addTo(rover.layer);
      if (st.sigma_m > 0) marsCircle(ll, st.sigma_m, { color: '#4cc9f0', weight: 1, fillOpacity: 0.12, interactive: false }).addTo(rover.layer);
      L.marker(ll, { icon: roverIcon(st.heading_deg), zIndexOffset: 1000 })
        .bindTooltip(`${esc(st.name || 'rover')} · sol ${esc(st.sol ?? '?')} · σ ${Math.round(st.sigma_m || 0)} m`).addTo(rover.layer);
      $('#rover-info').textContent = `${st.name || 'rover'} · ${st.lat.toFixed(5)}, ${st.lon.toFixed(5)} · hdg ${Math.round(st.heading_deg || 0)}° · σ ${(st.sigma_m || 0).toFixed(1)} m · ${st.source || ''}${st.error_m != null ? ` · error ${st.error_m} m` : ''}${st.odom_error_m != null ? ` (wheels only ${st.odom_error_m} m)` : ''} · ${r.age_s}s ago`;
      if ($('#rover-follow').checked) map.panTo(ll, { animate: false });
      rover.last = st;
    }
    rover.listeners.forEach((f) => f(rover.last, r.track || []));
  } catch (e) { $('#rover-info').textContent = 'service unreachable'; }
  rover.timer = setTimeout(pollRover, next);
}
pollRover();
$('#ov-rover').onchange = () => { clearTimeout(rover.timer); pollRover(); };

window.autonav = { rover, map, routesState, selectRoute, sun, getBasemap: () => basemap, getOverlays: () => [...active.values()].map((a) => a.layer),
  getDemOverlays: () => Object.values(demOv).filter(Boolean), LAYERS: () => LAYERS };

// Pinned demo routes: A -> B pairs where comm-aware planning avoids dead zones and rough ground
// (numbers: Safe rover, comm avoidance off -> normal, MOLA 463 m grids).
const DEMOS = [
  { id: 'gale-sharp', name: 'Gale · around Mount Sharp', from: [-4.857, 137.7022], to: [-5.0883, 137.6025],
    note: 'Curiosity site. Poor comms 5.5 → 1.0 km, rough 2.9 → 0 km, +2 km.' },
  { id: 'gusev-rim', name: 'Gusev · south-east rim', from: [-15.0259, 176.7899], to: [-15.1381, 176.6398],
    note: 'Spirit site. Dead zone 1.3 → 0 km, poor 3.5 → 0 km, rough 3.4 → 1.1 km, +1.6 km.' },
  { id: 'jezero-rim', name: 'Jezero · southern rim crossing', from: [18.2802, 77.3913], to: [18.1495, 77.3196],
    note: 'Perseverance site. Poor comms 2.7 → 0 km, rough 1.5 → 1.2 km, +1.2 km.' },
];
function planBetween(a, b, commWeight) {
  if (commWeight != null) $('#comm-weight').value = String(commWeight);
  if (!$('#ov-comms').checked) { $('#ov-comms').checked = true; refreshDem('comms'); syncLegends(); }
  setPoint('start', L.latLng(a[0], a[1]));
  setPoint('goal', L.latLng(b[0], b[1]));
  map.fitBounds(L.latLngBounds([a, b]).pad(0.3));
  document.querySelector('.tabs button[data-tab=navigation]').click();
  $('#route-go').click();
}
$('#demo-list').innerHTML = DEMOS.map((d) => `<div class="demo">
  <div><b>${esc(d.name)}</b><div class="hint">${esc(d.note)}</div></div>
  <button data-demo="${d.id}">Plan</button></div>`).join('');
$('#demo-list').onclick = (e) => {
  const d = DEMOS.find((x) => x.id === e.target.dataset.demo);
  if (d) planBetween(d.from, d.to, 1);
};

// Deep links: ?demo=<id> or ?from=lat,lon&to=lat,lon[&comm_weight=0|1|3] place A/B and plan.
{
  const qs = new URLSearchParams(location.search);
  const ll = (s) => (s || '').split(',').map(Number);
  const demo = DEMOS.find((x) => x.id === qs.get('demo'));
  const [a, b] = demo ? [demo.from, demo.to] : [ll(qs.get('from')), ll(qs.get('to'))];
  if (a.length === 2 && b.length === 2 && [...a, ...b].every(Number.isFinite))
    planBetween(a, b, qs.has('comm_weight') ? qs.get('comm_weight') : (demo ? 1 : null));
}
})();
