# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Build a small, replayable Perseverance Navcam stereo log from NASA's public raw-image API.

    build_m20_log --sols 118:125 --max-frames 40 --out /data/m20log/sol118

Output (all small): frames.jsonl (one line per stereo frame: rover pose, georeference, mast
pointing, rectified pinhole intrinsics, camera extrinsics), left/right rectified JPEGs,
meta.json (provenance/credits). Images: NASA/JPL-Caltech (public raw images).
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

import cv2
import numpy as np

from autonav_rover import cahv

API = 'https://mars.nasa.gov/rss/api/'
UA = {'User-Agent': 'autonav0-rover/0.1 (NASA Space Apps; offline replay log)'}
MARS_R = 3396190.0


def log(*a):
    print('[m20log]', *a, flush=True)


def http_json(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                raise
            log('retry', url[:80], e)
            time.sleep(2 + 2 * i)


def http_bytes(url):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            if i == 2:
                raise
            time.sleep(2 + 2 * i)


def query(sol, cam):
    imgs, page = [], 0
    while True:
        q = {'feed': 'raw_images', 'category': 'mars2020', 'feedtype': 'json', 'num': 100,
             'page': page, 'order': 'sol desc', 'search': '|' + cam,
             'condition_2': f'{sol}:sol:gte', 'condition_3': f'{sol}:sol:lte'}
        d = http_json(API + '?' + urllib.parse.urlencode(q))
        imgs += d.get('images', [])
        if len(d.get('images', [])) < 100 or page > 10:
            return imgs
        page += 1


def tup(s):
    return tuple(float(v) for v in s.strip('()').split(','))


def group_frames(images):
    """sclk -> list of tiles (dicts) for one camera."""
    g = defaultdict(list)
    for im in images:
        if im.get('camera', {}).get('camera_model_type', '') not in ('CAHVORE', 'CAHVOR', 'CAHV'):
            continue
        g[im['extended']['sclk']].append(im)
    return g


def tiles_layout(tiles):
    """Return [(tile, ox, oy, w, h, sf)] in model pixels and the mosaic size, or None."""
    out, W, H = [], 0, 0
    seen = set()
    for t in tiles:
        x0, y0, sw, sh = tup(t['extended']['subframeRect'])
        w, h = tup(t['extended']['dimension'])
        if (x0, y0) in seen:
            continue
        seen.add((x0, y0))
        sf = sw / w
        out.append((t, (x0 - 1) / sf, (y0 - 1) / sf, w, h, sf))
        W, H = max(W, (x0 - 1) / sf + w), max(H, (y0 - 1) / sf + h)
    if len({round(o[5], 3) for o in out}) != 1:
        return None, 0, 0
    # Require (near) full-sensor coverage: avoid odd sub-frames.
    if W * out[0][5] < 5000 or H * out[0][5] < 3700:
        return None, 0, 0
    return out, int(round(W)), int(round(H))


def mosaic(layout, W, H, k, variant):
    canvas = np.zeros((int(H * k), int(W * k), 3), np.uint8)
    for t, ox, oy, w, h, _ in layout:
        blob = http_bytes(t['image_files'][variant])
        img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise IOError('decode failed')
        tw, th = int(round(w * k)), int(round(h * k))
        img = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        x, y = int(round(ox * k)), int(round(oy * k))
        canvas[y:y + th, x:x + tw] = img[:canvas.shape[0] - y, :canvas.shape[1] - x]
    return canvas


def load_waypoints(src):
    try:
        if src.startswith('http'):
            gj = http_json(src)
        else:
            gj = json.load(open(src))
    except Exception as e:  # noqa: BLE001
        log('waypoints unavailable:', e)
        return {}
    wps = {}
    for f in gj.get('features', []):
        p = f.get('properties', {})
        if p.get('lat') is None:
            continue
        wps[(int(p['site']), int(p['drive']))] = (float(p['lat']), float(p['lon']))
    log(f'{len(wps)} M2020 waypoints for georeferencing')
    return wps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sols', default='118:125', help='sol or range a:b (inclusive)')
    ap.add_argument('--max-frames', type=int, default=40)
    ap.add_argument('--width', type=int, default=1024, help='output image width (px)')
    ap.add_argument('--variant', default='medium', choices=['medium', 'large', 'full_res'])
    ap.add_argument('--waypoints', default=os.environ.get('AUTONAV_URL', 'http://localhost:8080') + '/api/vector/m20_waypoints')
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    lo, _, hi = a.sols.partition(':')
    lo, hi = int(lo), int(hi or lo)
    out = a.out or os.path.join(os.environ.get('M20_LOG_DIR', 'm20log'), f'sol{lo:04d}-{hi:04d}')
    os.makedirs(out, exist_ok=True)

    wps = load_waypoints(a.waypoints)
    pairs = []
    for sol in range(lo, hi + 1):
        L, R = group_frames(query(sol, 'NAVCAM_LEFT')), group_frames(query(sol, 'NAVCAM_RIGHT'))
        rkeys = sorted(R, key=float)
        n = 0
        for ls, lt in L.items():
            # Right exposure is ~0.13 s after the left one.
            rs = min(rkeys, key=lambda k: abs(float(k) - float(ls)), default=None)
            if rs is None or abs(float(rs) - float(ls)) > 1.0:
                continue
            ll, W, H = tiles_layout(lt)
            rl, W2, H2 = tiles_layout(R[rs])
            if not ll or not rl or (W, H) != (W2, H2):
                continue
            pairs.append((float(ls), sol, ll, rl, W, H))
            n += 1
        log(f'sol {sol}: {n} stereo frames')
    pairs.sort(key=lambda p: p[0])
    if len(pairs) > a.max_frames:  # keep an evenly spaced subset
        idx = np.linspace(0, len(pairs) - 1, a.max_frames).round().astype(int)
        pairs = [pairs[i] for i in sorted(set(idx))]
    if not pairs:
        log('no stereo frames found; try another --sols range')
        return 1

    site_origin = {}  # site -> (lat0, lon0) of site-frame origin

    def process(i_pair):
        i, (sclk, sol, ll, rl, W, H) = i_pair
        k = a.width / W
        size = (int(W * k), int(H * k))
        L0, R0 = ll[0][0], rl[0][0]
        Kl, Rl, Cl = cahv.linearize(cahv.parse_model(L0['camera']['camera_model_component_list']), ll[0][1], ll[0][2], k)
        Kr, Rr, Cr = cahv.linearize(cahv.parse_model(R0['camera']['camera_model_component_list']), rl[0][1], rl[0][2], k)
        imgL, imgR = mosaic(ll, W, H, k, a.variant), mosaic(rl, W, H, k, a.variant)
        Rlr, T = cahv.stereo_extrinsics(Rl, Cl, Rr, Cr)
        # Rectifying rotations / virtual pinholes from the linearised (CAHV) cameras ...
        R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(Kl, None, Kr, None, size, Rlr, T, alpha=0)
        # ... but the pixel lookup goes through the full CAHVORE model (Navcam is near-equidistant).
        ml = cahv.shift_scale(cahv.parse_model(L0['camera']['camera_model_component_list']), ll[0][1], ll[0][2], k)
        mr = cahv.shift_scale(cahv.parse_model(R0['camera']['camera_model_component_list']), rl[0][1], rl[0][2], k)
        m1 = cahv.rectify_map(ml, Rl, R1, P1, size)
        m2 = cahv.rectify_map(mr, Rr, R2, P2, size)
        rectL = cv2.remap(imgL, m1[0], m1[1], cv2.INTER_LINEAR)
        rectR = cv2.remap(imgR, m2[0], m2[1], cv2.INTER_LINEAR)
        fl, fr = f'{i:04d}_left.jpg', f'{i:04d}_right.jpg'
        cv2.imwrite(os.path.join(out, fl), rectL, [cv2.IMWRITE_JPEG_QUALITY, 90])
        cv2.imwrite(os.path.join(out, fr), rectR, [cv2.IMWRITE_JPEG_QUALITY, 90])
        e = L0['extended']
        return {
            'index': i, 'sclk': sclk, 'utc': L0.get('date_taken_utc'), 'sol': sol,
            'lmst': L0.get('date_taken_mars'), 'site': int(L0['site']), 'drive': int(L0['drive']),
            'xyz_site': list(tup(e['xyz'])), 'attitude_wxyz': list(tup(L0['attitude'])),
            'mast_az': float(e['mastAz']), 'mast_el': float(e['mastEl']),
            'left': fl, 'right': fr, 'width': size[0], 'height': size[1],
            'P_left': P1.tolist(), 'P_right': P2.tolist(),
            'baseline_m': float(np.linalg.norm(T)),
            # Rectified left camera in the rover nav frame (FRD): rows = optical axes.
            'R_cam_from_rover': (R1 @ Rl).tolist(), 'C_rover': Cl.tolist(),
            'image_ids': [L0['imageid'], R0['imageid']],
            'credit': L0.get('credit', 'NASA/JPL-Caltech'),
        }

    frames = []
    with cf.ThreadPoolExecutor(4) as ex:
        for fr in ex.map(process, enumerate(pairs)):
            frames.append(fr)
            log(f"frame {fr['index']}: sol {fr['sol']} site {fr['site']} drive {fr['drive']} baseline {fr['baseline_m']:.3f} m")

    # Georeference: a frame whose (site, drive) matches a waypoint fixes that site's origin.
    for fr in frames:
        key = (fr['site'], fr['drive'])
        if fr['site'] not in site_origin and key in wps:
            lat, lon = wps[key]
            n, e_, _ = fr['xyz_site']
            lat0 = lat - math.degrees(n / MARS_R)
            lon0 = lon - math.degrees(e_ / (MARS_R * math.cos(math.radians(lat))))
            site_origin[fr['site']] = (lat0, lon0)
    for fr in frames:
        o = site_origin.get(fr['site'])
        if not o:
            # Fall back to the nearest waypoint of the same site.
            cands = [(abs(d - fr['drive']), ll_) for (s, d), ll_ in wps.items() if s == fr['site']]
            fr['lat'], fr['lon'] = (min(cands)[1] if cands else (None, None))
            fr['georef'] = 'nearest_waypoint' if cands else 'none'
            continue
        n, e_, _ = fr['xyz_site']
        fr['lat'] = o[0] + math.degrees(n / MARS_R)
        fr['lon'] = o[1] + math.degrees(e_ / (MARS_R * math.cos(math.radians(o[0]))))
        fr['georef'] = 'site_origin'

    with open(os.path.join(out, 'frames.jsonl'), 'w') as f:
        for fr in sorted(frames, key=lambda x: x['sclk']):
            f.write(json.dumps(fr) + '\n')
    json.dump({'mission': 'Mars 2020 Perseverance', 'instrument': 'Navcam (left/right)', 'sols': [lo, hi],
               'frames': len(frames), 'variant': a.variant, 'width': a.width,
               'source': 'https://mars.nasa.gov/mars2020/multimedia/raw-images/',
               'credit': 'NASA/JPL-Caltech', 'camera_model': 'CAHVORE (full model via sciimg-derived projection) -> rectified pinhole',
               'built_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},
              open(os.path.join(out, 'meta.json'), 'w'), indent=1)
    log(f'wrote {len(frames)} frames to {out}; georeferenced sites: {sorted(site_origin)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
