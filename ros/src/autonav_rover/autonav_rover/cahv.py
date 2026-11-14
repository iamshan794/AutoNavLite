# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""CAHV(ORE) camera-model helpers (JPL/Yakimovsky & Cunningham 1978; Gennery 2006).

Mars 2020 raw-image metadata publishes CAHVORE models in the rover navigation frame
(X forward, Y right, Z down). For stereo rectification we linearise to CAHV, i.e. an
ideal pinhole; the O/R/E (distortion) terms are ignored in this MVP.
"""
import numpy as np


def parse_model(s):
    """Parse '(..);(..);...' into a list of numpy vectors / floats."""
    out = []
    for part in s.split(';'):
        part = part.strip()
        if part.startswith('('):
            out.append(np.array([float(v) for v in part.strip('()').split(',')]))
        elif part:
            out.append(float(part))
    return out


def linearize(model, dx=0.0, dy=0.0, scale=1.0):
    """CAHV -> (K, R_cam_from_rover, C).

    dx/dy shift the principal point (e.g. sub-frame tile offset, in model pixels) and
    `scale` resamples the image. Rows of R are the camera axes (x right, y down, z
    forward = optical convention) expressed in rover-frame coordinates.
    """
    C, A, H, V = model[:4]
    hc, vc = A @ H, A @ V
    hs, vs = np.linalg.norm(np.cross(A, H)), np.linalg.norm(np.cross(A, V))
    Hp, Vp = (H - hc * A) / hs, (V - vc * A) / vs
    K = np.array([[hs * scale, 0, (hc + dx) * scale],
                  [0, vs * scale, (vc + dy) * scale],
                  [0, 0, 1.0]])
    R = np.vstack([Hp, Vp, A])
    # Re-orthonormalise (published models are rounded).
    u, _, vt = np.linalg.svd(R)
    return K, u @ vt, np.asarray(C, dtype=float)


def stereo_extrinsics(R_l, C_l, R_r, C_r):
    """OpenCV convention: x_r = R x_l + T."""
    R = R_r @ R_l.T
    T = R_r @ (C_l - C_r)
    return R, T


# --------------------------------------------------------------------------- full CAHVORE projection
# Ported from MarsRaw/sciimg `Cahvore::xyz_to_ls` (MIT, (c) 2021 Kevin M. Gill), for directions
# (points at infinity, where the entrance-pupil term E vanishes). Model components:
# C, A, H, V, O, R, E, pupil type, linearity (1 = perspective, 0 = fisheye/equidistant).

def shift_scale(model, dx=0.0, dy=0.0, scale=1.0):
    """Sub-frame shift (model pixels) then image rescale, like sciimg `shift().scale()`."""
    m = list(model)
    A = m[1]
    m[2] = (m[2] + dx * A) * scale
    m[3] = (m[3] + dy * A) * scale
    return m


def _chi_from_theta(theta, lin):
    if lin > 1e-10:
        return np.tan(np.minimum(lin * theta, np.pi / 2 - 1e-6)) / lin
    if lin < -1e-10:
        return np.sin(lin * theta) / lin
    return theta


def project_dirs(model, d):
    """Project unit directions d (N,3, rover frame) to (sample, line) with full CAHVORE/CAHVOR/CAHV."""
    A, H, V = model[1], model[2], model[3]
    A = A / np.linalg.norm(A)
    if len(model) >= 6 and isinstance(model[4], np.ndarray):
        O = model[4] / np.linalg.norm(model[4])
        R = model[5]
    else:
        O, R = A, np.zeros(3)
    lin = float(model[8]) if len(model) >= 9 else 1.0
    zeta = d @ O
    lam = d - np.outer(zeta, O)
    lm = np.linalg.norm(lam, axis=1)
    theta = np.arctan2(lm, zeta)
    chi = _chi_from_theta(theta, lin)
    chi_d = chi * (1.0 + R[0] + R[1] * chi ** 2 + R[2] * chi ** 4)
    with np.errstate(invalid='ignore', divide='ignore'):
        k = np.where(lm > 1e-12, chi_d / lm, 0.0)
    rp = O[None, :] + lam * k[:, None]
    alpha = rp @ A
    bad = alpha <= 1e-9
    alpha = np.where(bad, np.nan, alpha)
    return rp @ H / alpha, rp @ V / alpha


def rectify_map(model, R_cam_from_rover, R_rect, P, size):
    """cv2.remap tables: rectified pixel -> raw (mosaic) pixel through the full camera model."""
    w, h = size
    u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    pix = np.stack([u.ravel(), v.ravel(), np.ones(u.size)], axis=0)
    rays_rect = np.linalg.inv(P[:, :3]) @ pix                 # rectified camera frame
    rays_rover = R_cam_from_rover.T @ (R_rect.T @ rays_rect)  # -> raw camera -> rover frame
    rays_rover /= np.linalg.norm(rays_rover, axis=0)
    s, l = project_dirs(model, rays_rover.T)
    return s.reshape(h, w).astype(np.float32), l.reshape(h, w).astype(np.float32)


def quat_to_rot(q):
    """(w, x, y, z) -> 3x3 rotation matrix."""
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def rot_to_quat(R):
    """3x3 -> (x, y, z, w) (ROS order)."""
    t = np.trace(R)
    if t > 0:
        s = 0.5 / np.sqrt(t + 1.0)
        w, x, y, z = 0.25 / s, (R[2, 1] - R[1, 2]) * s, (R[0, 2] - R[2, 0]) * s, (R[1, 0] - R[0, 1]) * s
    else:
        i = int(np.argmax(np.diag(R)))
        if i == 0:
            s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
            w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
        elif i == 1:
            s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
            w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
        else:
            s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
            w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    q = np.array([x, y, z, w])
    return q / np.linalg.norm(q)


# Frame conventions: Mars site frame is NED-like (X north, Y east, Z down), rover nav frame is
# FRD (X forward, Y right, Z down). ROS uses ENU (map) and FLU (base_link).
NED_TO_ENU = np.array([[0, 1, 0], [1, 0, 0], [0, 0, -1.0]])
FRD_TO_FLU = np.diag([1.0, -1.0, -1.0])


def rover_rot_enu(q_site_rover):
    """Rotation base_link(FLU) -> map(ENU) from the M2020 attitude quaternion (w,x,y,z)."""
    return NED_TO_ENU @ quat_to_rot(np.asarray(q_site_rover, dtype=float)) @ FRD_TO_FLU.T
