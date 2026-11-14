#!/usr/bin/env python3
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Train the orbital landform classifier (DoMars16k, 15 classes) used for the terrain-class layers.

    python3 scripts/terrain_train.py --dataset DIR [--backbone vits14|vitb14] [--fractions 0.01,0.1,1]

Frozen DINOv2 backbone (Oquab et al. 2023, Apache-2.0) + linear probe (multinomial logistic
regression). Data: DoMars16k (Wilhelm et al. 2020, CC BY 4.0), 200x200 px CTX crops, as
repackaged by Mars-Bench (HF Mirali33/mb-domars16k, revision pinned in DOMARS_REV; train/val/test
parquet files go in --dataset). Runs a data-scaling sweep, picks weight decay on val, reports test
accuracy / macro-F1 and saves the head to data/terrain/models/domars_<backbone>.npz.
Parquet is read with pyarrow 17 (baked into the rover image's perception venv; 17.x keeps NumPy 1.x).
"""
import argparse
import io
import json
import os
import sys
import time

import numpy as np
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.environ.get('AUTONAV_DATA', os.path.join(ROOT, 'data'))
DINOV2_REPO = 'facebookresearch/dinov2:7764ea0f912e53c92e82eb78a2a1631e92725fc8'
DOMARS_HF = 'Mirali33/mb-domars16k'
DOMARS_REV = '4d79cf7a0fc98fab76d436f6434e4ce3eb78cd85'


def log(*a):
    print('[train]', *a, flush=True)


def load_backbone(name, device):
    import torch
    repo, ref = DINOV2_REPO.split(':')
    model = torch.hub.load(f'{repo}:{ref}', f'dinov2_{name}', trust_repo=True).to(device).eval()
    return model


def embed(model, images, device, size=224, batch=128, tta=False):
    """CLS + mean patch token, L2-normalised. images: uint8 (N,H,W) grayscale."""
    import torch
    mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
    out = []
    for i in range(0, len(images), batch):
        x = torch.from_numpy(images[i:i + batch]).to(device).float().div(255).unsqueeze(1).expand(-1, 3, -1, -1)
        x = torch.nn.functional.interpolate(x, size=(size, size), mode='bilinear', align_corners=False)
        x = (x - mean) / std
        views = [x] if not tta else [x, x.flip(3), x.flip(2), x.rot90(1, (2, 3))]
        acc = 0
        with torch.inference_mode(), torch.autocast(device.type, enabled=device.type == 'cuda'):
            for v in views:
                f = model.forward_features(v)
                acc = acc + torch.cat([f['x_norm_clstoken'], f['x_norm_patchtokens'].mean(1)], 1).float()
        out.append(torch.nn.functional.normalize(acc, dim=1).cpu().numpy())
    return np.concatenate(out)


def degrade(imgs, scale):
    """Simulate coarser imagery (e.g. Trek z12 ~10 m/px, z11 ~20 m/px vs CTX ~6 m/px)."""
    if scale >= 1:
        return imgs
    n = max(8, int(round(200 * scale)))
    return np.stack([np.asarray(Image.fromarray(im).resize((n, n), Image.BILINEAR)) for im in imgs])


def read_split(path):
    sys.path.insert(0, os.environ.get('AUTONAV_PYLIB', '/pylib'))
    import pyarrow.parquet as pq
    t = pq.read_table(path)
    imgs = np.stack([np.asarray(Image.open(io.BytesIO(r['bytes'])).convert('L').resize((200, 200)))
                     for r in t.column('image').to_pylist()])
    return imgs, np.array(t.column('label').to_pylist())


def train_probe(Xtr, ytr, Xva, yva, n_cls, device, wds=(1e-5, 1e-4, 1e-3, 1e-2), epochs=300):
    """Full-batch multinomial logistic regression (Adam); weight decay chosen on val."""
    import torch
    Xt, yt = torch.from_numpy(Xtr).to(device), torch.from_numpy(ytr).to(device)
    Xv = torch.from_numpy(Xva).to(device)
    best = None
    for wd in wds:
        lin = torch.nn.Linear(Xt.shape[1], n_cls).to(device)
        opt = torch.optim.Adam(lin.parameters(), lr=1e-2, weight_decay=wd)
        for _ in range(epochs):
            opt.zero_grad()
            torch.nn.functional.cross_entropy(lin(Xt * 10), yt).backward()
            opt.step()
        with torch.no_grad():
            acc = (lin(Xv * 10).argmax(1).cpu().numpy() == yva).mean()
        if best is None or acc > best[0]:
            best = (acc, wd, lin.weight.detach().cpu().numpy() * 10, lin.bias.detach().cpu().numpy())
    return best


def metrics(W, b, X, y, n_cls):
    pred = (X @ W.T + b).argmax(1)
    f1 = []
    for c in range(n_cls):
        tp, fp, fn = ((pred == c) & (y == c)).sum(), ((pred == c) & (y != c)).sum(), ((pred != c) & (y == c)).sum()
        f1.append(2 * tp / max(1, 2 * tp + fp + fn))
    conf = np.zeros((n_cls, n_cls), int)
    np.add.at(conf, (y, pred), 1)
    return float((pred == y).mean()), float(np.mean(f1)), [round(float(v), 3) for v in f1], conf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', required=True, help='dir with train/val/test parquet + mapping.json')
    ap.add_argument('--backbone', default='vits14', choices=['vits14', 'vitb14'])
    ap.add_argument('--fractions', default='0.01,0.05,0.1,0.25,0.5,1.0')
    ap.add_argument('--tta', action='store_true', help='average features over flips/rotation')
    ap.add_argument('--scale', type=float, default=1.0, help='downsample images first (simulate coarser GSD)')
    ap.add_argument('--size', type=int, default=224, help='backbone input size')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    import torch
    dev = torch.device('cuda' if a.device in ('auto', 'cuda') and torch.cuda.is_available() else 'cpu')
    mapping = json.load(open(os.path.join(a.dataset, 'mapping.json')))
    codes = [mapping[str(i)][0] for i in range(len(mapping))]
    names = [mapping[str(i)][1] for i in range(len(mapping))]
    n_cls = len(names)

    tag = f'{a.backbone}{"_tta" if a.tta else ""}' + (f'_s{a.scale:g}' if a.scale < 1 else '') + \
        (f'_i{a.size}' if a.size != 224 else '')
    cache = os.path.join(a.dataset, f'feat_{tag}.npz')
    if os.path.exists(cache):
        z = np.load(cache)
        F = {k: (z[f'{k}_X'], z[f'{k}_y']) for k in ('train', 'val', 'test')}
        log('features from cache', cache)
    else:
        model = load_backbone(a.backbone, dev)
        F = {}
        for split in ('train', 'val', 'test'):
            t0 = time.time()
            imgs, y = read_split(os.path.join(a.dataset, f'{split}.parquet'))
            F[split] = (embed(model, degrade(imgs, a.scale), dev, size=a.size, tta=a.tta), y)
            log(f'{split}: {len(y)} images embedded in {time.time() - t0:.0f}s on {dev}')
        np.savez(cache, **{f'{k}_{n}': v for k, (X, y) in F.items() for n, v in (('X', X), ('y', y))})

    (Xtr, ytr), (Xva, yva), (Xte, yte) = F['train'], F['val'], F['test']
    rng = np.random.default_rng(a.seed)
    sweep = []
    for frac in [float(f) for f in a.fractions.split(',')]:
        idx = np.concatenate([rng.permutation(np.where(ytr == c)[0])[:max(2, int(round(frac * (ytr == c).sum())))]
                              for c in range(n_cls)])
        va_acc, wd, W, b = train_probe(Xtr[idx], ytr[idx], Xva, yva, n_cls, dev)
        acc, mf1, f1, conf = metrics(W, b, Xte, yte, n_cls)
        sweep.append({'fraction': frac, 'n_train': int(len(idx)), 'val_acc': round(float(va_acc), 4),
                      'test_acc': round(acc, 4), 'test_macro_f1': round(mf1, 4), 'weight_decay': wd})
        log(f'{a.backbone} frac={frac:<5} n={len(idx):5d}  val {va_acc * 100:5.1f}%  test {acc * 100:5.1f}%  macroF1 {mf1:.3f}')
    log('per-class test F1 (full data): ' + ', '.join(f'{c}={v}' for c, v in zip(codes, f1)))
    out = os.path.join(DATA, 'terrain', 'models')
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f'domars_{tag}.npz')
    np.savez(path, W=W.astype(np.float32), b=b.astype(np.float32), codes=np.array(codes), names=np.array(names))
    json.dump({'backbone': f'DINOv2 {a.backbone} ({DINOV2_REPO})', 'head': 'linear probe', 'tta': a.tta,
               'scale': a.scale, 'input_size': a.size, 'feature': 'L2norm(concat(CLS, mean patch token))',
               'dataset': f'DoMars16k via {DOMARS_HF}@{DOMARS_REV}', 'classes': dict(zip(codes, names)),
               'scaling': sweep, 'per_class_f1': dict(zip(codes, f1)), 'confusion': conf.tolist(),
               'citation': 'Wilhelm et al. 2020, Remote Sens. 12(23) 3981 (DoMars16k, CC BY 4.0); '
                           'Oquab et al. 2023 (DINOv2, Apache-2.0)'},
              open(path[:-4] + '.json', 'w'), indent=1)
    log('saved', path)


if __name__ == '__main__':
    sys.exit(main())
