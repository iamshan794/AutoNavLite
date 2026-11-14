#!/usr/bin/env python3
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Precompute terrain-class layers (DoMars16k landforms) from Trek CTX mosaics, once, offline.

    python3 scripts/terrain_map.py [--tier sites|ez|all] [--regions Gale,Jezero,...] [--device auto]

Tiers (all imagery: NASA Trek CTX block-adjusted mosaics, `*_CTX_BlockAdj_dd`):
  sites : 4 rover sites (Gale, Jezero, Gusev, Meridiani), 60 km box, zoom 12 (~10 m/px), 250 m cells
  ez    : every regional CTX mosaic in the Trek catalog (37; they cover the candidate human
          Exploration Zones), central 100 km box, zoom 11 (~20 m/px), 500 m cells
Each cell is classified from a ~1.2 km window (the DoMars16k sample footprint) with a frozen DINOv2
ViT-B/14 + linear head trained by scripts/terrain_train.py at the matching ground resolution.

Outputs (small, shipped with the service): data/terrain/terrain_<region>_<tier>.{img,json}
(int16 BE class codes, 0 = no data, 16 = uncertain), a GeoTIFF copy (.tif, Mars 2000 sphere,
lat/lon) and a preview JPG. Tiles are fetched straight from Trek into a disposable cache dir.
"""
import argparse
import concurrent.futures as cf
import io
import json
import math
import os
import struct
import sys
import time
import urllib.request

import numpy as np
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.environ.get('AUTONAV_DATA', os.path.join(ROOT, 'data'))
DINOV2_REPO = 'facebookresearch/dinov2:7764ea0f912e53c92e82eb78a2a1631e92725fc8'
TREK = 'https://trek.nasa.gov/tiles/Mars/EQ'
M_PER_DEG = 3396190.0 * math.pi / 180
TILE = 256
WINDOW_M = 1200.0
UNCERTAIN, UNCERTAIN_P = 16, 0.4

# DoMars16k thematic groups (Wilhelm et al. 2020) -> level 1; the 15 classes are level 2.
GROUPS = {'ael': 'aeolian bedforms', 'aec': 'aeolian bedforms',
          'cli': 'topographic landforms', 'rid': 'topographic landforms', 'fsf': 'topographic landforms',
          'sfe': 'topographic landforms',
          'fsg': 'slope features', 'fse': 'slope features', 'fss': 'slope features',
          'cra': 'impact landforms', 'sfx': 'impact landforms',
          'smo': 'basic terrain', 'tex': 'basic terrain', 'rou': 'basic terrain', 'mix': 'basic terrain'}
COLORS = {'ael': '#f2a541', 'aec': '#f7c873', 'cli': '#8b1e3f', 'rid': '#c0392b', 'fsf': '#2e86de',
          'sfe': '#a569bd', 'fsg': '#16a085', 'fse': '#48c9b0', 'fss': '#7d6608', 'cra': '#e67e22',
          'sfx': '#f0b27a', 'smo': '#abebc6', 'tex': '#d5dbdb', 'rou': '#7f8c8d', 'mix': '#bfc9ca'}
# Heuristic rover trafficability (0..1) used by the route planner; editable in the output JSON.
# soft = sinkage / sand-trap risk, rock = wheel damage / roughness.
PLANNER = {'ael': (0.0, 1.0), 'aec': (0.0, 1.0), 'cli': (1.0, 0.0), 'rid': (0.6, 0.0), 'fsf': (0.1, 0.3),
           'sfe': (0.3, 0.0), 'fsg': (0.7, 0.2), 'fse': (0.1, 0.5), 'fss': (0.8, 0.3), 'cra': (0.5, 0.0),
           'sfx': (0.4, 0.0), 'smo': (0.0, 0.0), 'tex': (0.3, 0.0), 'rou': (1.0, 0.0), 'mix': (0.2, 0.1)}
ROVER_SITES = {'Gale': (-4.59, 137.40), 'Jezero': (18.44, 77.45), 'Gusev': (-14.57, 175.47),
               'S_Meridiani_Sinus_Planium': (-1.95, -5.53)}
TIERS = {'sites': dict(zoom=12, box_km=60, cell_m=250, head='domars_vitb14_s0.6'),
         'ez': dict(zoom=11, box_km=100, cell_m=500, head='domars_vitb14_s0.3')}


def log(*a):
    print('[terrain-map]', *a, flush=True)


# ------------------------------------------------------------------------------------------ imagery
def fetch_tile(layer, ext, z, r, c, cache):
    path = os.path.join(cache, layer, str(z), str(r), f'{c}.{ext}')
    if os.path.exists(path):
        blob = open(path, 'rb').read()
    else:
        url = f'{TREK}/{layer}/1.0.0/default/default028mm/{z}/{r}/{c}.{ext}'
        blob = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'autonav0-terrain-map/1.0'})
                blob = urllib.request.urlopen(req, timeout=60).read()
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
                time.sleep(2 ** attempt)
            except Exception:  # noqa: BLE001
                time.sleep(2 ** attempt)
        if blob is None:
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(blob)
    try:
        return np.asarray(Image.open(io.BytesIO(blob)).convert('L'))
    except Exception:  # noqa: BLE001
        return None


def mosaic(layer, ext, z, s, w, n, e, cache, workers=8):
    span = 180.0 / 2 ** z
    r0, r1 = int((90 - n) // span), int((90 - s) // span)
    c0, c1 = int((w + 180) // span), int((e + 180) // span)
    img = np.zeros(((r1 - r0 + 1) * TILE, (c1 - c0 + 1) * TILE), np.uint8)
    jobs = [(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]
    missing = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for (r, c), t in zip(jobs, ex.map(lambda rc: fetch_tile(layer, ext, z, rc[0], rc[1], cache), jobs)):
            if t is None or t.shape != (TILE, TILE):
                missing += 1
                continue
            img[(r - r0) * TILE:(r - r0 + 1) * TILE, (c - c0) * TILE:(c - c0 + 1) * TILE] = t
    log(f'{layer} z{z}: {len(jobs)} tiles ({missing} missing)')
    return img, 90 - r0 * span, -180 + c0 * span, span / TILE


# ------------------------------------------------------------------------------------------ model
class Classifier:
    def __init__(self, head, device):
        import torch
        self.t = torch
        meta = json.load(open(os.path.join(DATA, 'terrain', 'models', head + '.json')))
        z = np.load(os.path.join(DATA, 'terrain', 'models', head + '.npz'))
        self.codes = [str(c) for c in z['codes']]
        self.names = [str(n) for n in z['names']]
        self.meta = meta
        self.dev = torch.device('cuda' if device in ('auto', 'cuda') and torch.cuda.is_available() else 'cpu')
        backbone = meta['backbone'].split()[1]
        repo, ref = DINOV2_REPO.split(':')
        self.model = torch.hub.load(f'{repo}:{ref}', f'dinov2_{backbone}', trust_repo=True).to(self.dev).eval()
        self.W = torch.from_numpy(z['W']).to(self.dev)
        self.b = torch.from_numpy(z['b']).to(self.dev)
        self.size = meta.get('input_size', 224)
        self.mean = torch.tensor([0.485, 0.456, 0.406], device=self.dev).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device=self.dev).view(1, 3, 1, 1)
        log(f'head {head} ({meta["scaling"][-1]["test_acc"] * 100:.1f}% DoMars16k test) on {self.dev}')

    def probs(self, windows):
        t = self.t
        x = t.from_numpy(windows).to(self.dev).float().div(255).unsqueeze(1).expand(-1, 3, -1, -1)
        x = t.nn.functional.interpolate(x, size=(self.size, self.size), mode='bilinear', align_corners=False)
        x = (x - self.mean) / self.std
        with t.inference_mode(), t.autocast(self.dev.type, enabled=self.dev.type == 'cuda'):
            f = self.model.forward_features(x)
            v = t.cat([f['x_norm_clstoken'], f['x_norm_patchtokens'].mean(1)], 1).float()
        v = t.nn.functional.normalize(v, dim=1)
        return t.softmax(v @ self.W.T + self.b, 1).cpu().numpy()


# ------------------------------------------------------------------------------------------ output
def write_geotiff(path, codes, lat_max, lon_min, deg):
    """Minimal GeoTIFF (int16, geographic lat/lon on the IAU Mars 2000 sphere)."""
    from PIL import TiffImagePlugin
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (deg, deg, 0.0)                         # ModelPixelScale
    info[33922] = (0.0, 0.0, 0.0, lon_min, lat_max, 0.0)  # ModelTiepoint (pixel corner)
    # GeoKeyDirectory: RasterPixelIsArea, user-defined geographic CS with a 3396190 m sphere.
    info[34735] = (1, 1, 0, 7, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 32767, 2050, 0, 1, 32767,
                   2054, 0, 1, 9102, 2056, 0, 1, 32767, 2057, 34736, 1, 0)
    info[34736] = (3396190.0,)                            # GeoDoubleParams: semi-major axis
    info.tagtype[33550] = info.tagtype[33922] = info.tagtype[34736] = 12  # DOUBLE
    info.tagtype[34735] = 3                                # SHORT
    Image.fromarray(codes.astype(np.int16)).save(path, tiffinfo=info, compression='tiff_deflate')


def classify_region(clf, name, layer, ext, tier, center, cache, batch):
    cfg = TIERS[tier]
    lat, lon = center
    half_lat = cfg['box_km'] * 500 / M_PER_DEG
    half_lon = half_lat / max(0.05, math.cos(math.radians(lat)))
    img, top, left, deg_px = mosaic(layer, ext, cfg['zoom'], lat - half_lat, lon - half_lon,
                                    lat + half_lat, lon + half_lon, cache)
    gsd = deg_px * M_PER_DEG
    win = int(round(WINDOW_M / gsd))
    # Equirectangular pixels shrink east-west by cos(lat): widen windows so they stay ~1.2 x 1.2 km on
    # the ground (resized to a square for the model, like the training crops).
    win_x = int(round(win / max(0.2, math.cos(math.radians(lat)))))
    stride = max(1, int(round(cfg['cell_m'] / gsd)))
    # Crop to the requested box, then lay windows centred on a regular cell grid.
    y0 = int((top - (lat + half_lat)) / deg_px)
    x0 = int((lon - half_lon - left) / deg_px)
    ny = int(2 * half_lat / deg_px) // stride
    nx = int(2 * half_lon / deg_px) // stride
    pad = max(win, win_x) // 2
    P = np.pad(img, pad)
    oy, ox = pad - win // 2, pad - win_x // 2
    codes = np.zeros((ny, nx), np.int16)
    conf = np.zeros((ny, nx), np.float32)
    cells = [(i, j) for i in range(ny) for j in range(nx)]
    t0 = time.time()
    for k in range(0, len(cells), batch):
        chunk = cells[k:k + batch]
        wins = np.stack([P[oy + y0 + i * stride + stride // 2: oy + y0 + i * stride + stride // 2 + win,
                           ox + x0 + j * stride + stride // 2: ox + x0 + j * stride + stride // 2 + win_x]
                         for i, j in chunk])
        valid = (wins > 0).mean((1, 2)) > 0.8
        if valid.any():
            p = clf.probs(wins[valid])
            best, pmax = p.argmax(1), p.max(1)
            for (i, j), c, pm in zip([c for c, v in zip(chunk, valid) if v], best, pmax):
                codes[i, j] = c + 1 if pm >= UNCERTAIN_P else UNCERTAIN
                conf[i, j] = pm
    dt = time.time() - t0
    lat_max = top - y0 * deg_px  # cell (i, j) covers pixels [y0 + i*stride, y0 + (i+1)*stride)
    lon_min = left + x0 * deg_px
    cell_deg = stride * deg_px
    valid = codes > 0
    frac = {clf.codes[c]: round(float((codes == c + 1).sum() / max(1, valid.sum())), 3) for c in range(len(clf.codes))}
    frac['uncertain'] = round(float((codes == UNCERTAIN).sum() / max(1, valid.sum())), 3)
    log(f'{name} [{tier}] {ny}x{nx} cells ({cfg["cell_m"]} m) in {dt:.0f}s, coverage {valid.mean() * 100:.0f}%, '
        f'mean p {conf[valid].mean() if valid.any() else 0:.2f}')

    out = os.path.join(DATA, 'terrain')
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, f'terrain_{name}_{tier}')
    with open(base + '.img', 'wb') as f:
        f.write(struct.pack(f'>{codes.size}h', *codes.ravel().tolist()))
    classes = {str(i + 1): clf.names[i] for i in range(len(clf.codes))}
    classes[str(UNCERTAIN)] = 'uncertain'
    json.dump({
        'name': f'{name.replace("_", " ")} terrain classes ({tier}, {cfg["cell_m"]} m)',
        'file': os.path.basename(base) + '.img', 'ppd': 1.0 / cell_deg, 'lat_max': lat_max,
        'lon_min': lon_min % 360.0, 'width': nx, 'height': ny, 'type': 'int16be', 'global': False,
        'nodata': 0, 'categorical': True, 'level': 2, 'classes': classes,
        'codes': {str(i + 1): clf.codes[i] for i in range(len(clf.codes))},
        'groups': {str(i + 1): GROUPS[clf.codes[i]] for i in range(len(clf.codes))},
        'colors': {**{str(i + 1): COLORS[clf.codes[i]] for i in range(len(clf.codes))}, str(UNCERTAIN): '#808080'},
        'planner': {str(i + 1): {'rock': PLANNER[clf.codes[i]][0], 'soft': PLANNER[clf.codes[i]][1]}
                    for i in range(len(clf.codes))},
        'imagery': f'{layer} z{cfg["zoom"]} (~{gsd:.0f} m/px), window {WINDOW_M:.0f} m',
        'model': clf.meta['backbone'] + ' + ' + clf.meta['head'],
        'test_accuracy': clf.meta['scaling'][-1]['test_acc'],
        'uncertain_below_p': UNCERTAIN_P, 'mean_confidence': round(float(conf[valid].mean()), 3) if valid.any() else None,
        'class_fractions': frac, 'tier': tier,
        'citation': 'Classes: DoMars16k (Wilhelm et al. 2020, CC BY 4.0); model: DINOv2 (Oquab et al. 2023, '
                    'Apache-2.0); imagery: MRO CTX (Malin et al. 2007) via NASA Solar System Treks; AutoNav-0',
    }, open(base + '.json', 'w'), indent=1)
    write_geotiff(base + '.tif', codes, lat_max, lon_min, cell_deg)
    # Preview: imagery | classes.
    rgb = np.zeros((ny, nx, 3), np.uint8)
    for c in range(1, UNCERTAIN + 1):
        hexc = COLORS[clf.codes[c - 1]] if c <= len(clf.codes) else '#808080'
        rgb[codes == c] = [int(hexc[k:k + 2], 16) for k in (1, 3, 5)]
    sub = img[y0:y0 + ny * stride, x0:x0 + nx * stride]
    a = Image.fromarray(sub).convert('RGB').resize((nx * 4, ny * 4))
    b = Image.blend(a, Image.fromarray(rgb).resize((nx * 4, ny * 4), Image.NEAREST), 0.55)
    both = Image.new('RGB', (a.width * 2 + 6, a.height))
    both.paste(a, (0, 0))
    both.paste(b, (a.width + 6, 0))
    both.thumbnail((1600, 800))
    both.save(base + '_preview.jpg', quality=82)


def regions(tier):
    layers = json.load(open(os.path.join(DATA, 'catalog', 'layers.json')))
    ez = {l['id'].replace('_CTX_BlockAdj_dd', ''): l for l in layers if l['id'].endswith('_CTX_BlockAdj_dd')}
    out = []
    for name, l in sorted(ez.items()):
        b = l['bbox']
        if tier == 'sites':
            if name in ROVER_SITES:
                out.append((name, l, ROVER_SITES[name]))
        else:
            out.append((name, l, ((b[1] + b[3]) / 2, (b[0] + b[2]) / 2)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tier', default='all', choices=['sites', 'ez', 'all'])
    ap.add_argument('--regions', help='comma-separated region names (default: all in tier)')
    ap.add_argument('--tile-cache', default=os.environ.get('AUTONAV_TILE_TMP', '/tmp/autonav-terrain-tiles'))
    ap.add_argument('--skip-existing', action='store_true')
    ap.add_argument('--batch', type=int, default=256)
    ap.add_argument('--device', default=os.environ.get('AUTONAV_DEVICE', 'auto'))
    a = ap.parse_args()
    want = set(a.regions.split(',')) if a.regions else None
    for tier in (['sites', 'ez'] if a.tier == 'all' else [a.tier]):
        clf = Classifier(TIERS[tier]['head'], a.device)
        for name, layer, center in regions(tier):
            if want and name not in want:
                continue
            if a.skip_existing and os.path.exists(os.path.join(DATA, 'terrain', f'terrain_{name}_{tier}.json')):
                continue
            classify_region(clf, name, layer['id'], layer.get('ext', 'png'), tier, center, a.tile_cache, a.batch)
    log('done')


if __name__ == '__main__':
    sys.exit(main())
