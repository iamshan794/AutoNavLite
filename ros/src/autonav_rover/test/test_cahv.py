# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
import numpy as np

from autonav_rover import cahv


def make_cahv(C, R, f, cx, cy):
    """Ideal pinhole -> CAHV (rows of R: camera x-right, y-down, z-forward in rover frame)."""
    x, y, A = R
    return [np.asarray(C, float), A, f * x + cx * A, f * y + cy * A]


def test_linearize_roundtrip_and_tile_shift():
    R = np.eye(3)[[1, 2, 0]]  # camera looks along rover +X (forward), x-right = rover +Y, y-down = rover +Z
    K, Rc, C = cahv.linearize(make_cahv([1, 0.5, -2], R, 1500.0, 640.0, 480.0), dx=10, dy=-20, scale=0.5)
    assert np.allclose(K, [[750, 0, 325], [0, 750, 230], [0, 0, 1]])
    assert np.allclose(Rc, R) and np.allclose(C, [1, 0.5, -2])


def test_stereo_extrinsics_baseline():
    R = np.eye(3)[[1, 2, 0]]
    _, Rl, Cl = cahv.linearize(make_cahv([1, 0.3, -2], R, 1500, 640, 480))
    _, Rr, Cr = cahv.linearize(make_cahv([1, 0.724, -2], R, 1500, 640, 480))  # 42.4 cm to the right
    Rlr, T = cahv.stereo_extrinsics(Rl, Cl, Rr, Cr)
    assert np.allclose(Rlr, np.eye(3))
    assert np.allclose(T, [-0.424, 0, 0])  # OpenCV: right camera at -baseline along x


def test_rover_rot_enu_heading():
    # Rover facing east in the site (NED) frame: yaw +90 deg about down axis.
    q = [np.cos(np.pi / 4), 0, 0, np.sin(np.pi / 4)]
    R = cahv.rover_rot_enu(q)
    assert np.allclose(R @ [1, 0, 0], [1, 0, 0], atol=1e-9)    # forward -> east
    assert np.allclose(R @ [0, 0, 1], [0, 0, 1], atol=1e-9)    # up stays up
    assert np.allclose(cahv.quat_to_rot(np.roll(cahv.rot_to_quat(R), 1)), R)


def test_full_model_matches_pinhole_when_perspective_and_undistorted():
    R = np.eye(3)[[1, 2, 0]]
    m = make_cahv([0, 0, 0], R, 1500.0, 640.0, 480.0)
    m = m + [R[2], np.zeros(3), np.zeros(3), 1.0, 1.0]  # O=A, R=0, E=0, perspective
    d = np.array([[1.0, 0.2, -0.1], [1.0, -0.3, 0.25]])
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    s, l = cahv.project_dirs(m, d)
    K = np.array([[1500, 0, 640], [0, 1500, 480], [0, 0, 1.0]])
    uv = (K @ (R @ d.T)).T
    assert np.allclose(s, uv[:, 0] / uv[:, 2]) and np.allclose(l, uv[:, 1] / uv[:, 2])


def test_fisheye_linearity_bends_off_axis_rays():
    R = np.eye(3)[[1, 2, 0]]
    m = make_cahv([0, 0, 0], R, 1500.0, 640.0, 480.0) + [R[2], np.zeros(3), np.zeros(3), 2.0, 0.0]
    d = np.array([[np.cos(0.6), np.sin(0.6), 0.0]])  # 34 deg off-axis, to the right
    s, _ = cahv.project_dirs(m, d)
    assert np.isclose(s[0], 640 + 1500 * 0.6)  # equidistant: r = f * theta (not f * tan(theta))
