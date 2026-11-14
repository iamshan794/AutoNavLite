# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Perception backends with GPU models and CPU fallbacks behind one interface.

Stereo   : RAFT-Stereo (MIT, Lipson et al. 2021)       -> SGBM + left-right check (OpenCV, CPU)
Features : XFeat (Apache-2.0, Potje et al. CVPR 2024)  -> ORB (OpenCV, CPU)
Flow     : RAFT small (torchvision, BSD-3; Teed & Deng 2020) -> pyramidal Lucas-Kanade (OpenCV, CPU)

`make_*(name='auto', device='auto')` returns the best available backend; every backend exposes
`.name` (e.g. "raft_stereo/cuda") so nodes can report what actually runs. Models live under
$AUTONAV_MODELS (default /opt/models), baked into the rover image.
"""
import os
import sys
import time

import cv2
import numpy as np

MODELS = os.environ.get('AUTONAV_MODELS', '/opt/models')


def _torch_device(pref):
    """-> (torch, torch.device) or (None, reason)."""
    if pref == 'cpu-only':
        return None, 'cpu-only requested'
    try:
        import torch
    except ImportError:
        return None, 'torch not installed'
    if pref in ('auto', 'cuda') and torch.cuda.is_available():
        return torch, torch.device('cuda')
    if pref == 'cuda':
        return None, 'CUDA not available'
    return torch, torch.device('cpu')


def log(msg):
    print(f'[perception] {msg}', file=sys.stderr, flush=True)


# =============================================================================== stereo
def lr_consistency(dl, dr, tol=1.0):
    """Invalidate left disparities whose right-image match does not point back (occlusions, mismatches)."""
    h, w = dl.shape
    xs = np.arange(w)[None, :].repeat(h, 0)
    fin = np.isfinite(dl)
    xr = np.round(xs - np.where(fin, dl, 0)).astype(np.int32)
    ok = (xr >= 0) & (xr < w) & fin
    back = np.full_like(dl, np.nan)
    rows = np.arange(h)[:, None].repeat(w, 1)
    back[ok] = dr[rows[ok], xr[ok]]
    out = dl.copy()
    out[~ok | ~(np.abs(back - dl) <= tol)] = np.nan
    return out


class SGBMStereo:
    """Semi-global matching (Hirschmüller 2008) with a left-right check. Runs anywhere."""

    def __init__(self, max_disp=256):
        self.name = 'sgbm/cpu'
        nd = int(np.ceil(max_disp / 16) * 16)
        common = dict(blockSize=5, P1=8 * 3 * 25, P2=32 * 3 * 25, uniquenessRatio=12,
                      speckleWindowSize=100, speckleRange=2, disp12MaxDiff=1,
                      mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        self.left = cv2.StereoSGBM_create(minDisparity=0, numDisparities=nd, **common)
        self.right = cv2.StereoSGBM_create(minDisparity=-nd, numDisparities=nd, **common)

    def disparity(self, left_rgb, right_rgb):
        L = cv2.cvtColor(left_rgb, cv2.COLOR_RGB2GRAY)
        R = cv2.cvtColor(right_rgb, cv2.COLOR_RGB2GRAY)
        dl = self.left.compute(L, R).astype(np.float32) / 16.0
        dr = -self.right.compute(R, L).astype(np.float32) / 16.0  # right-view disparity, positive
        dl[dl <= 0] = np.nan
        dr[dr <= 0] = np.nan
        return lr_consistency(dl, dr)


class RaftStereo:
    """RAFT-Stereo (princeton-vl). Default checkpoint: ETH3D (grayscale outdoor/indoor) - closest to Navcam."""

    VARIANTS = {
        'eth3d': dict(ckpt='raftstereo-eth3d.pth', args={}, iters=16),  # 16 its: ~2x faster than 32, same depth
        'realtime': dict(ckpt='raftstereo-realtime.pth', iters=7,
                         args=dict(shared_backbone=True, n_downsample=3, n_gru_layers=2, slow_fast_gru=True)),
    }

    def __init__(self, torch, device, variant='eth3d', iters=None, lr_check=True):
        import argparse
        root = os.path.join(MODELS, 'raft_stereo')
        if root not in sys.path:
            sys.path.insert(0, root)
        from core.raft_stereo import RAFTStereo  # noqa: E402  (vendored, pinned commit)
        v = self.VARIANTS[variant]
        a = argparse.Namespace(hidden_dims=[128] * 3, corr_implementation='reg', shared_backbone=False,
                               corr_levels=4, corr_radius=4, n_downsample=2, context_norm='batch',
                               slow_fast_gru=False, n_gru_layers=3, mixed_precision=device.type == 'cuda')
        for k, val in v['args'].items():
            setattr(a, k, val)
        model = torch.nn.DataParallel(RAFTStereo(a))
        state = torch.load(os.path.join(root, v['ckpt']), map_location='cpu', weights_only=True)
        model.load_state_dict(state)
        self.model = model.module.to(device).eval()
        if device.type == 'cuda':
            torch.backends.cudnn.benchmark = True  # fixed input size -> autotuned kernels
        self.torch, self.device, self.iters = torch, device, iters or v['iters']
        self.lr_check = lr_check
        self.amp = device.type == 'cuda'
        self.name = f'raft_stereo-{variant}/{device.type}'

    def _run(self, a, b):
        t = self.torch
        x1 = t.from_numpy(a).permute(2, 0, 1).float()[None].to(self.device)
        x2 = t.from_numpy(b).permute(2, 0, 1).float()[None].to(self.device)
        h, w = x1.shape[-2:]
        ph, pw = (-h) % 32, (-w) % 32
        pad = (pw // 2, pw - pw // 2, ph // 2, ph - ph // 2)
        x1 = t.nn.functional.pad(x1, pad, mode='replicate')
        x2 = t.nn.functional.pad(x2, pad, mode='replicate')
        with t.inference_mode(), t.autocast(self.device.type, enabled=self.amp):
            _, flow = self.model(x1, x2, iters=self.iters, test_mode=True)
        flow = flow[..., pad[2]:pad[2] + h, pad[0]:pad[0] + w]
        return (-flow[0, 0]).float().cpu().numpy()  # RAFT-Stereo outputs negative disparity

    def disparity(self, left_rgb, right_rgb):
        dl = self._run(left_rgb, right_rgb)
        dl[dl <= 0.25] = np.nan
        if not self.lr_check:
            return dl
        # Right-view disparity by running on horizontally flipped, swapped images.
        dr = self._run(np.ascontiguousarray(right_rgb[:, ::-1]), np.ascontiguousarray(left_rgb[:, ::-1]))[:, ::-1]
        dr[dr <= 0.25] = np.nan
        return lr_consistency(dl, np.ascontiguousarray(dr), tol=1.5)


def make_stereo(name='auto', device='auto', **kw):
    if name in ('auto', 'raft_stereo'):
        torch, dev = _torch_device('cpu-only' if device == 'cpu' and name == 'auto' else device)
        if torch is not None and (dev.type == 'cuda' or name == 'raft_stereo'):
            try:
                return RaftStereo(torch, dev, **{k: v for k, v in kw.items() if k in ('variant', 'iters', 'lr_check')})
            except Exception as e:  # noqa: BLE001
                log(f'RAFT-Stereo unavailable ({e}); falling back to SGBM')
        elif name == 'raft_stereo':
            log(f'RAFT-Stereo needs torch ({dev}); falling back to SGBM')
    return SGBMStereo(kw.get('max_disp', 256))


# =============================================================================== features
class ORBFeatures:
    def __init__(self, n=4000):
        self.name = 'orb/cpu'
        self.orb = cv2.ORB_create(n, fastThreshold=10)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    def detect(self, rgb):
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        k, d = self.orb.detectAndCompute(g, None)
        if d is None:
            return np.zeros((0, 2), np.float32), None
        return np.float32([p.pt for p in k]), d

    def match(self, d1, d2):
        if d1 is None or d2 is None:
            return np.zeros((0, 2), int)
        m = [x for x in self.bf.match(d1, d2) if x.distance < 64]
        return np.array([[x.queryIdx, x.trainIdx] for x in m], int).reshape(-1, 2)


class XFeatFeatures:
    def __init__(self, torch, device, top_k=4096):
        root = os.path.join(MODELS, 'xfeat')
        if root not in sys.path:
            sys.path.insert(0, root)
        from modules.xfeat import XFeat  # noqa: E402  (vendored, pinned commit)
        self.x = XFeat(weights=os.path.join(root, 'weights', 'xfeat.pt'), top_k=top_k)
        self.x.dev = device
        self.x.net = self.x.net.to(device)
        self.torch = torch
        self.name = f'xfeat/{device.type}'

    def detect(self, rgb):
        with self.torch.inference_mode():
            out = self.x.detectAndCompute(rgb, top_k=self.x.top_k)[0]
        return out['keypoints'].cpu().numpy().astype(np.float32), out['descriptors']

    def match(self, d1, d2):
        if d1 is None or d2 is None or len(d1) == 0 or len(d2) == 0:
            return np.zeros((0, 2), int)
        with self.torch.inference_mode():
            i1, i2 = self.x.match(d1, d2, min_cossim=0.82)
        return np.stack([i1.cpu().numpy(), i2.cpu().numpy()], 1).astype(int)


def make_features(name='auto', device='auto'):
    if name in ('auto', 'xfeat'):
        torch, dev = _torch_device(device)
        if torch is not None:
            try:
                return XFeatFeatures(torch, dev)  # XFeat is light enough to run on CPU too
            except Exception as e:  # noqa: BLE001
                log(f'XFeat unavailable ({e}); falling back to ORB')
    return ORBFeatures()


# =============================================================================== optical flow
class LKFlow:
    """Sparse pyramidal Lucas-Kanade with forward-backward check: returns (pts0, pts1)."""

    def __init__(self):
        self.name = 'lk/cpu'

    def track(self, rgb0, rgb1, pts0=None):
        g0, g1 = cv2.cvtColor(rgb0, cv2.COLOR_RGB2GRAY), cv2.cvtColor(rgb1, cv2.COLOR_RGB2GRAY)
        if pts0 is None:
            pts0 = cv2.goodFeaturesToTrack(g0, 3000, 0.01, 7).reshape(-1, 2)
        p0 = pts0.reshape(-1, 1, 2).astype(np.float32)
        kw = dict(winSize=(21, 21), maxLevel=5, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
        p1, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, p0, None, **kw)
        pb, st2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, p1, None, **kw)
        ok = (st.ravel() == 1) & (st2.ravel() == 1) & (np.linalg.norm((pb - p0).reshape(-1, 2), axis=1) < 1.0)
        return p0.reshape(-1, 2)[ok], p1.reshape(-1, 2)[ok]


class RaftFlow:
    """Dense RAFT (torchvision raft_small) sampled at points, with forward-backward consistency."""

    def __init__(self, torch, device):
        from torchvision.models.optical_flow import Raft_Small_Weights, raft_small
        w = Raft_Small_Weights.DEFAULT
        self.model = raft_small(weights=w).to(device).eval()
        self.tf = w.transforms()
        self.torch, self.device = torch, device
        self.name = f'raft_flow/{device.type}'

    def _flow(self, a, b):
        t = self.torch
        x1 = t.from_numpy(a).permute(2, 0, 1)[None]
        x2 = t.from_numpy(b).permute(2, 0, 1)[None]
        h, w = a.shape[:2]
        H, W = (h // 8) * 8, (w // 8) * 8
        x1 = t.nn.functional.interpolate(x1.float(), (H, W), mode='bilinear') / 255.0
        x2 = t.nn.functional.interpolate(x2.float(), (H, W), mode='bilinear') / 255.0
        x1, x2 = self.tf(x1, x2)
        with t.inference_mode():
            f = self.model(x1.to(self.device), x2.to(self.device), num_flow_updates=12)[-1][0]
        f = t.nn.functional.interpolate(f[None], (h, w), mode='bilinear')[0]
        f[0] *= w / W
        f[1] *= h / H
        return f.permute(1, 2, 0).cpu().numpy()

    def track(self, rgb0, rgb1, pts0=None):
        if pts0 is None:
            g = cv2.cvtColor(rgb0, cv2.COLOR_RGB2GRAY)
            pts0 = cv2.goodFeaturesToTrack(g, 3000, 0.01, 7).reshape(-1, 2)
        fw, bw = self._flow(rgb0, rgb1), self._flow(rgb1, rgb0)
        h, w = fw.shape[:2]
        p = np.clip(np.round(pts0).astype(int), [0, 0], [w - 1, h - 1])
        p1 = pts0 + fw[p[:, 1], p[:, 0]]
        q = np.round(p1).astype(int)
        inside = (q[:, 0] >= 0) & (q[:, 0] < w) & (q[:, 1] >= 0) & (q[:, 1] < h)
        back = np.full_like(p1, np.inf)
        back[inside] = p1[inside] + bw[q[inside, 1], q[inside, 0]]
        ok = inside & (np.linalg.norm(back - pts0, axis=1) < 1.0)
        return pts0[ok].astype(np.float32), p1[ok].astype(np.float32)


def make_flow(name='auto', device='auto'):
    if name in ('auto', 'raft'):
        torch, dev = _torch_device(device)
        if torch is not None and (dev.type == 'cuda' or name == 'raft'):
            try:
                return RaftFlow(torch, dev)
            except Exception as e:  # noqa: BLE001
                log(f'RAFT flow unavailable ({e}); falling back to LK')
    return LKFlow()


# =============================================================================== geometry helpers
def disparity_to_depth(disp, fx, baseline, zmin=0.5, zmax=40.0):
    with np.errstate(divide='ignore', invalid='ignore'):
        z = fx * baseline / disp
    z[~np.isfinite(z) | (z < zmin) | (z > zmax)] = np.nan
    return z.astype(np.float32)


def backproject(depth, K, step=1):
    """depth (HxW, NaN invalid) -> (N,3) camera points (x right, y down, z forward) and pixel indices."""
    h, w = depth.shape
    v, u = np.mgrid[0:h:step, 0:w:step]
    z = depth[::step, ::step]
    ok = np.isfinite(z)
    u, v, z = u[ok], v[ok], z[ok]
    x = (u - K[0, 2]) * z / K[0, 0]
    y = (v - K[1, 2]) * z / K[1, 1]
    return np.stack([x, y, z], 1).astype(np.float32), (v, u)


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t) * 1000
