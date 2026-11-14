# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""ROS 2 -> Rerun bridge with a ready-made blueprint.

3D panel tabs:
  * Reconstruction - every stereo cloud placed with the NASA-reported pose (clean, consistent map).
    Clouds are stored per frame as static data, so memory stays bounded however long the replay runs.
  * Localisation   - truth vs wheel odometry vs RTAB-Map vs learned VO trajectories, plus the latest
    cloud placed with the *estimated* pose (misalignment there = localisation error, by design).
Side panels: Navcam with VO inlier keypoints, error / latency plots, perception backend log.

Memory: heavy data (clouds, trajectories, images) is logged as *static* (latest value only, overwritten in
place) and rate-limited, so neither the gRPC server nor the browser viewer grows with replay time; only
small data (scalars, poses, text) keeps a history for the time series.

Runs from the Rerun venv (NumPy 2) and connects to a Rerun gRPC server (`rerun --serve-web`).
"""
import json
import math
import os
import time

import numpy as np
import rclpy
import rerun as rr
import rerun.blueprint as rrb
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image, NavSatFix, PointCloud2
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

MARS_R = 3396190.0
COLORS = {'truth': [76, 201, 240], 'odom': [255, 159, 28], 'slam': [125, 220, 125], 'vo': [199, 125, 255]}
LABELS = {'truth': 'NASA-reported pose (truth)', 'odom': 'Wheel odometry (drifting)',
          'slam': 'RTAB-Map VSLAM', 'vo': 'Learned-feature VO / re-localisation'}


def cloud_xyzrgb(msg, max_points=None):
    f = {x.name: x.offset for x in msg.fields}
    n = msg.width * msg.height
    if n == 0 or not all(k in f for k in 'xyz'):
        return None, None
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(n, msg.point_step)
    xyz = np.stack([raw[:, f[k]:f[k] + 4].copy().view(np.float32)[:, 0] for k in 'xyz'], 1)
    rgb = None
    if 'rgb' in f or 'rgba' in f:
        o = f.get('rgb', f.get('rgba'))
        rgb = raw[:, o:o + 3][:, ::-1].copy()  # packed 0x00RRGGBB little-endian: bytes are B,G,R
    ok = np.isfinite(xyz).all(1)
    xyz, rgb = xyz[ok], (rgb[ok] if rgb is not None else None)
    if max_points and len(xyz) > max_points:
        idx = np.random.default_rng(0).choice(len(xyz), max_points, replace=False)
        xyz, rgb = xyz[idx], (rgb[idx] if rgb is not None else None)
    return xyz, rgb


def voxel_down(xyz, rgb, size):
    _, idx = np.unique(np.floor(xyz / size).astype(np.int64), axis=0, return_index=True)
    return xyz[idx], (rgb[idx] if rgb is not None else None)


def quat_rot(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def blueprint():
    recon = rrb.Spatial3DView(
        origin='world', name='Reconstruction (NASA poses)', background=[18, 12, 10],
        contents=['world/reconstruction/**', 'world/truth/**', 'world/path/truth'])
    loc = rrb.Spatial3DView(
        origin='world', name='Localisation (truth / odom / VSLAM / VO)', background=[18, 12, 10],
        contents=['world/path/**', 'world/estimate/**', 'world/truth/rover/**', 'world/rtabmap_cloud'])
    return rrb.Blueprint(
        rrb.Horizontal(
            rrb.Tabs(recon, loc, active_tab=0),
            rrb.Vertical(
                rrb.Spatial2DView(origin='navcam', name='Navcam left + VO inliers'),
                rrb.TimeSeriesView(origin='metrics/error', name='Position error vs truth (m)'),
                rrb.TimeSeriesView(origin='metrics/latency', name='Perception latency (ms)'),
                rrb.TextLogView(origin='log', name='Frames & backends'),
                row_shares=[3, 2, 1.3, 1.2]),
            column_shares=[3, 2]),
        rrb.TimePanel(state='collapsed'),
        rrb.SelectionPanel(state='collapsed'),
    )


class RerunBridge(Node):
    def __init__(self):
        super().__init__('rerun_bridge')
        self.declare_parameter('grpc_url', os.environ.get('RERUN_GRPC_URL', 'rerun+http://localhost:9876/proxy'))
        self.declare_parameter('voxel_m', 0.08)
        rr.init('autonav0_rover', spawn=False)
        url = self.get_parameter('grpc_url').value
        self.bp = blueprint()
        rr.connect_grpc(url, default_blueprint=self.bp)
        rr.send_blueprint(self.bp, make_active=True, make_default=True)
        self.get_logger().info(f'logging to Rerun at {url} (blueprint sent)')
        rr.log('world', rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        for who, col in (('truth', COLORS['truth']), ('estimate', COLORS['slam'])):
            rr.log(f'world/{who}/rover/body', rr.Boxes3D(half_sizes=[[1.5, 1.35, 0.35]], centers=[[0, 0, 0.9]],
                                                         colors=[col]), static=True)
        for k in ('odom', 'slam', 'vo'):
            rr.log(f'metrics/error/{k}', rr.SeriesLines(names=LABELS[k], colors=[COLORS[k]]), static=True)
        rr.log('metrics/latency/stereo', rr.SeriesLines(names='stereo depth', colors=[[240, 200, 80]]), static=True)
        rr.log('metrics/latency/vo', rr.SeriesLines(names='VO / re-localisation', colors=[COLORS['vo']]), static=True)

        self.tf = Buffer()
        self.tfl = TransformListener(self.tf, self)
        self.paths = {k: [] for k in COLORS}
        self.cam_info = None
        self.stamp_index = {}  # stereo stamp (ns) -> frame index (images, clouds and meta share stamps)
        self.backends = {}
        self.origin = None
        self.last_log = {}  # entity -> wall time of last heavy log (rate limiting)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        sub = self.create_subscription
        sub(NavSatFix, '/rover/map_origin', lambda m: setattr(self, 'origin', (m.latitude, m.longitude)), latched)
        sub(CameraInfo, '/rover/navcam/left/camera_info', lambda m: setattr(self, 'cam_info', m), 5)
        sub(Image, '/rover/navcam/left/image_rect', self.on_image, 5)
        sub(PointCloud2, '/rover/stereo_cloud', self.on_cloud, 5)
        sub(PointCloud2, '/rtabmap/cloud_map', self.on_map_cloud, 2)
        sub(PoseStamped, '/rover/pose_truth', lambda m: self.on_pose('truth', m.pose, m.header.stamp), 10)
        sub(Odometry, '/rover/wheel_odom', lambda m: self.on_pose('odom', m.pose.pose, m.header.stamp), 10)
        sub(Odometry, '/rover/vo/odom', lambda m: self.on_pose('vo', m.pose.pose, m.header.stamp), 10)
        sub(String, '/rover/frame_meta', self.on_meta, 10)
        sub(String, '/rover/vo/status', self.on_vo_status, 10)
        sub(String, '/rover/perception/stereo', self.on_stereo_status, 10)
        sub(String, '/autonav/route_geojson', self.on_route, latched)
        self.create_timer(0.5, self.on_timer)
        self.create_timer(30.0, lambda: rr.send_blueprint(self.bp, make_active=False, make_default=True))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def t(stamp):
        rr.set_time('ros_time', timestamp=stamp.sec + stamp.nanosec * 1e-9)

    @staticmethod
    def ns(stamp):
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    def lookup(self, parent, child, stamp=None):
        try:
            tr = self.tf.lookup_transform(parent, child, Time.from_msg(stamp) if stamp else Time())
        except Exception:  # noqa: BLE001
            return None
        p, q = tr.transform.translation, tr.transform.rotation
        return np.array([p.x, p.y, p.z]), np.array([q.x, q.y, q.z, q.w])

    def due(self, entity, period_s):
        now = time.monotonic()
        if now - self.last_log.get(entity, 0.0) < period_s:
            return False
        self.last_log[entity] = now
        return True

    def add_path_point(self, key, p, stamp):
        path = self.paths[key]
        if not path or np.linalg.norm(np.asarray(p) - path[-1]) > 0.05:
            path.append(np.asarray(p, dtype=float))
            if len(path) > 3000:
                del path[:len(path) - 3000]
        if len(path) > 1 and self.due(f'path/{key}', 1.0):
            rr.log(f'world/path/{key}', rr.LineStrips3D([np.array(path)], colors=[COLORS[key]], radii=0.08,
                                                       labels=[LABELS[key]], show_labels=False), static=True)
        truth = self.paths['truth'][-1] if self.paths['truth'] else None
        if key != 'truth' and truth is not None and self.due(f'err/{key}', 0.5):
            self.t(stamp)
            rr.log(f'metrics/error/{key}', rr.Scalars(float(np.hypot(p[0] - truth[0], p[1] - truth[1]))))

    # ------------------------------------------------------------ callbacks
    def on_image(self, m):
        if self.cam_info is None:
            return
        self.t(m.header.stamp)
        img = np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.width, -1)
        jpg = rr.Image(img).compress(jpeg_quality=80)
        P = np.array(self.cam_info.p).reshape(3, 4)
        cam = self.lookup('world', 'navcam_left_rect_truth', m.header.stamp)
        if cam is not None:
            rr.log('world/truth/navcam', rr.Transform3D(translation=cam[0], quaternion=rr.Quaternion(xyzw=cam[1])),
                   static=True)
            rr.log('world/truth/navcam', rr.Pinhole(image_from_camera=P[:, :3], width=m.width, height=m.height,
                                                    camera_xyz=rr.ViewCoordinates.RDF, image_plane_distance=1.0),
                   static=True)
            rr.log('world/truth/navcam/image', jpg, static=True)
        rr.log('navcam/image', jpg, static=True)
        rr.log('navcam/inliers', rr.Points2D(np.zeros((0, 2))), static=True)

    def on_cloud(self, m):
        xyz, rgb = cloud_xyzrgb(m)
        if xyz is None:
            return
        self.t(m.header.stamp)
        idx = self.stamp_index.get(self.ns(m.header.stamp))
        truth = self.lookup('world', 'navcam_left_rect_truth', m.header.stamp)
        if truth is not None and idx is not None:
            w = (quat_rot(truth[1]) @ xyz.T).T + truth[0]
            w, c = voxel_down(w, rgb, float(self.get_parameter('voxel_m').value))
            # Static, keyed by frame: a revisit overwrites, so memory stays bounded.
            rr.log(f'world/reconstruction/frame_{idx:04d}', rr.Points3D(w, colors=c, radii=0.03), static=True)
        est = self.lookup('map', 'navcam_left_rect', m.header.stamp)
        if est is None:
            est = self.lookup('odom', 'navcam_left_rect', m.header.stamp)
        if est is not None:
            sub = np.random.default_rng(1).choice(len(xyz), min(len(xyz), 30000), replace=False)
            w = (quat_rot(est[1]) @ xyz[sub].T).T + est[0]
            rr.log('world/estimate/cloud', rr.Points3D(w, colors=rgb[sub] if rgb is not None else None, radii=0.03),
                   static=True)

    def on_map_cloud(self, m):
        if not self.due('rtabmap_cloud', 5.0):
            return
        xyz, rgb = cloud_xyzrgb(m, max_points=60000)
        if xyz is not None:
            rr.log('world/rtabmap_cloud', rr.Points3D(xyz, colors=rgb, radii=0.04), static=True)

    def on_pose(self, key, pose, stamp):
        p = [pose.position.x, pose.position.y, pose.position.z]
        if key == 'truth' and self.due('truth/rover', 0.5):
            q = [pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w]
            self.t(stamp)
            rr.log('world/truth/rover', rr.Transform3D(translation=p, quaternion=rr.Quaternion(xyzw=q)))
        self.add_path_point(key, p, stamp)

    def on_meta(self, m):
        meta = json.loads(m.data)
        if 'stamp_ns' in meta:
            self.stamp_index[meta['stamp_ns']] = meta['index']
            if len(self.stamp_index) > 500:
                for k in sorted(self.stamp_index)[:250]:
                    del self.stamp_index[k]
        rr.log('log', rr.TextLog(f"frame {meta.get('index')}: sol {meta.get('sol')} site {meta.get('site')} "
                                 f"drive {meta.get('drive')} mast az {meta.get('mast_az', 0):.0f} "
                                 f"el {meta.get('mast_el', 0):.0f}", level='INFO'))

    def on_stereo_status(self, m):
        s = json.loads(m.data)
        rr.log('metrics/latency/stereo', rr.Scalars(s['ms']))
        if self.backends.get('stereo') != s['backend']:
            self.backends['stereo'] = s['backend']
            rr.log('log', rr.TextLog(f"stereo backend: {s['backend']} (baseline {s['baseline_m']} m)", level='INFO'))

    def on_vo_status(self, m):
        s = json.loads(m.data)
        rr.log('metrics/latency/vo', rr.Scalars(s['ms']))
        if 'inlier_px' in s:
            rr.log('navcam/inliers', rr.Points2D(s['inlier_px'], colors=[COLORS['vo']], radii=3), static=True)
        if self.backends.get('vo') != s['backend']:
            self.backends['vo'] = s['backend']
            rr.log('log', rr.TextLog(f"VO backend: {s['backend']}", level='INFO'))
        rr.log('log', rr.TextLog(f"VO {s['method']}: {s['inliers']}/{s['matches']} inliers, "
                                 f"err {s.get('vo_error_m')} m (wheels {s.get('odom_error_m')} m)", level='DEBUG'))

    def on_route(self, m):
        if not self.origin:
            return
        gj = json.loads(m.data)
        lat0, lon0 = self.origin
        z = self.paths['truth'][-1][2] if self.paths['truth'] else 0.0
        pts = [[math.radians(lo - lon0) * MARS_R * math.cos(math.radians(lat0)), math.radians(la - lat0) * MARS_R, z]
               for lo, la, *_ in gj['geometry']['coordinates']]
        rr.log('world/path/planned', rr.LineStrips3D([pts], colors=[[255, 255, 255]], radii=0.15,
                                                     labels=[gj['properties'].get('label', 'planned route')]), static=True)

    def on_timer(self):
        now = self.get_clock().now().to_msg()
        slam = self.lookup('map', 'base_link')
        est = slam if slam is not None else self.lookup('odom', 'base_link')
        if est is not None:
            self.t(now)
            rr.log('world/estimate/rover', rr.Transform3D(translation=est[0], quaternion=rr.Quaternion(xyzw=est[1])))
        if slam is not None:
            self.add_path_point('slam', slam[0], now)


def main():
    rclpy.init()
    node = RerunBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
