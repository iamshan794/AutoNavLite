# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Replays a Perseverance Navcam stereo log as if the rover were producing it in real time.

Publishes
  /rover/navcam/{left,right}/image_rect, camera_info   rectified stereo (same stamp)
  /rover/wheel_odom (nav_msgs/Odometry) + TF odom->base_link   dead reckoning with injected drift
  /rover/pose_truth (PoseStamped, map) + TF map->base_link_truth  NASA-reported pose
  /rover/fix (NavSatFix)                     NASA-reported lat/lon (orbital localisation stand-in)
  /rover/frame_meta (std_msgs/String JSON)   sol/site/drive/mast pointing of the current frame
  /joint_states                              mast pan/tilt for the URDF model
  TF base_link->navcam_left_rect             per-frame camera extrinsics (mast moves)
  TF world->base_link_truth                  NASA-reported pose ('world' = start pose; RTAB-Map owns 'map')
"""
import json
import math
import os

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import CameraInfo, Image, JointState, NavSatFix
from std_msgs.msg import String
from std_srvs.srv import Empty
from tf2_ros import TransformBroadcaster

from autonav_rover import cahv

MARS_R = 3396190.0


def find_log(root):
    if os.path.isfile(os.path.join(root, 'frames.jsonl')):
        return root
    cands = sorted(d for d in (os.path.join(root, x) for x in os.listdir(root) if not x.startswith('.'))
                   if os.path.isfile(os.path.join(d, 'frames.jsonl'))) if os.path.isdir(root) else []
    return cands[0] if cands else None


def tf_msg(stamp, parent, child, t, q):
    m = TransformStamped()
    m.header.stamp, m.header.frame_id, m.child_frame_id = stamp, parent, child
    m.transform.translation.x, m.transform.translation.y, m.transform.translation.z = map(float, t)
    m.transform.rotation.x, m.transform.rotation.y, m.transform.rotation.z, m.transform.rotation.w = map(float, q)
    return m


def slerp(q0, q1, u):
    q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
    d = float(np.dot(q0, q1))
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + u * (q1 - q0)
        return q / np.linalg.norm(q)
    th = math.acos(d)
    return (math.sin((1 - u) * th) * q0 + math.sin(u * th) * q1) / math.sin(th)


class LogPlayer(Node):
    def __init__(self):
        super().__init__('m20_log_player')
        self.declare_parameter('log_dir', os.environ.get('M20_LOG_DIR', '/data/m20log'))
        self.declare_parameter('frame_period_s', 3.0)   # wall-clock seconds between stereo frames
        self.declare_parameter('rate_hz', 10.0)         # odometry / TF rate
        self.declare_parameter('loop', True)
        self.declare_parameter('odom_scale_error', 0.03)   # wheel slip: +3 % distance
        self.declare_parameter('odom_yaw_drift_deg_per_m', 0.05)
        self.declare_parameter('odom_sigma_frac', 0.02)    # 1-sigma growth (fraction of distance)
        root = self.get_parameter('log_dir').value
        d = find_log(root)
        if not d:
            raise RuntimeError(f'no frames.jsonl under {root}; run build_m20_log first')
        self.dir = d
        self.frames = [json.loads(line) for line in open(os.path.join(d, 'frames.jsonl'))]
        self.frames = [f for f in self.frames if f.get('lat') is not None] or self.frames
        self.get_logger().info(f'replaying {len(self.frames)} frames from {d}')
        self.period = float(self.get_parameter('frame_period_s').value)
        self.loop = bool(self.get_parameter('loop').value)

        o = self.frames[0]
        self.origin = (o['lat'], o['lon'])
        self.poses = [self.frame_pose(f) for f in self.frames]

        sensor = QoSProfile(depth=5)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_l = self.create_publisher(Image, '/rover/navcam/left/image_rect', sensor)
        self.pub_r = self.create_publisher(Image, '/rover/navcam/right/image_rect', sensor)
        self.pub_li = self.create_publisher(CameraInfo, '/rover/navcam/left/camera_info', sensor)
        self.pub_ri = self.create_publisher(CameraInfo, '/rover/navcam/right/camera_info', sensor)
        self.pub_odom = self.create_publisher(Odometry, '/rover/wheel_odom', 10)
        self.pub_truth = self.create_publisher(PoseStamped, '/rover/pose_truth', 10)
        self.pub_fix = self.create_publisher(NavSatFix, '/rover/fix', 10)
        self.pub_meta = self.create_publisher(String, '/rover/frame_meta', 10)
        self.pub_js = self.create_publisher(JointState, '/joint_states', 10)
        self.pub_origin = self.create_publisher(NavSatFix, '/rover/map_origin', latched)
        self.tfb = TransformBroadcaster(self)

        org = NavSatFix()
        org.header.frame_id = 'map'
        org.latitude, org.longitude = self.origin
        self.pub_origin.publish(org)

        # rtabmap_slam exposes it as a private service (~/set_mode_localization); the name depends on
        # the launch namespace, so try both forms.
        self._freeze_clis = [self.create_client(Empty, n) for n in
                             ('/rtabmap/rtabmap/set_mode_localization', '/rtabmap/set_mode_localization')]
        self.t0 = self.get_clock().now()
        self.cur = -1
        self.reset_odom()
        self.timer = self.create_timer(1.0 / float(self.get_parameter('rate_hz').value), self.tick)

    # ------------------------------------------------------------------ poses
    def frame_pose(self, f):
        lat0, lon0 = self.origin
        if f.get('lat') is not None:
            x = math.radians(f['lon'] - lon0) * MARS_R * math.cos(math.radians(lat0))
            y = math.radians(f['lat'] - lat0) * MARS_R
        else:
            x, y = f['xyz_site'][1], f['xyz_site'][0]
        z = -f['xyz_site'][2]
        R = cahv.rover_rot_enu(f['attitude_wxyz'])
        return np.array([x, y, z]), cahv.rot_to_quat(R), R

    def reset_odom(self):
        self.odom_p = self.poses[0][0].copy()
        self.odom_yaw_bias = 0.0
        self.odom_dist = 0.0
        self.last_truth = self.poses[0][0].copy()

    def timeline(self, t):
        """Elapsed frame periods -> fractional frame index. With `loop`, the log plays forward then
        backward (ping-pong): the rover drives back along its own path, so motion stays physical and
        revisited places give VSLAM genuine loop closures to correct the accumulated odometry drift."""
        n = len(self.frames) - 1
        if n <= 0:
            return 0.0
        leg = int(t // n)
        if not self.loop and leg >= 1:
            return None
        u = t - leg * n
        if leg != getattr(self, '_leg', 0):
            self._leg = leg
            if leg == 1:
                self.freeze_map()
            self.get_logger().info(f"leg {leg}: driving {'back' if leg % 2 else 'forward'} along the log")
        return u if leg % 2 == 0 else n - u

    def freeze_map(self):
        """After the first pass the whole log has been mapped: switch RTAB-Map to localisation mode so the
        endless replay re-localises against that map instead of growing it forever (RAM + database).
        Retries for up to 60 s because the service may not be discovered yet."""
        self._freeze_tries = 0

        def attempt():
            self._freeze_tries += 1
            ready = [c for c in self._freeze_clis if c.service_is_ready()]
            if ready:
                ready[0].call_async(Empty.Request())
                self.get_logger().info(f'first pass mapped: RTAB-Map -> localisation mode ({ready[0].srv_name})')
                self._freeze_timer.cancel()
            elif self._freeze_tries >= 60:
                self.get_logger().warn('RTAB-Map localisation service not found (slam:=false?); map keeps growing')
                self._freeze_timer.cancel()
        self._freeze_timer = self.create_timer(1.0, attempt)
        attempt()

    def truth_at(self, s):
        """Interpolated truth pose at fractional frame index s."""
        i = min(int(s), len(self.frames) - 1)
        j = min(i + 1, len(self.frames) - 1)
        u = s - i if j > i else 0.0
        p = (1 - u) * self.poses[i][0] + u * self.poses[j][0]
        q = slerp(self.poses[i][1], self.poses[j][1], u)
        return p, q, (i if u < 0.5 else j)

    # ------------------------------------------------------------------ main loop
    def tick(self):
        now = self.get_clock().now()
        stamp = now.to_msg()
        s = self.timeline((now - self.t0).nanoseconds * 1e-9 / self.period)
        if s is None:
            return
        # A stereo frame is "captured" when the timeline reaches its index: snap the pose to it so
        # the images and the pose/TF they are stamped with agree exactly.
        new_frame = None
        prev = getattr(self, '_prev_s', None)
        if prev is None:
            new_frame = int(round(s))
        else:
            lo, hi = min(prev, s), max(prev, s)
            ks = [k for k in range(int(math.ceil(lo)), int(math.floor(hi)) + 1) if k != prev]
            if ks:
                new_frame = ks[-1] if s >= prev else ks[0]
        if new_frame is not None:
            s = float(new_frame)
        self._prev_s = s
        p, q, i = self.truth_at(s)

        # Dead reckoning: integrate truth increments with scale error + growing heading bias.
        step = p - self.last_truth
        self.last_truth = p.copy()
        dist = float(np.linalg.norm(step[:2]))
        self.odom_dist += dist
        self.odom_yaw_bias += math.radians(float(self.get_parameter('odom_yaw_drift_deg_per_m').value)) * dist
        c, sn = math.cos(self.odom_yaw_bias), math.sin(self.odom_yaw_bias)
        k = 1.0 + float(self.get_parameter('odom_scale_error').value)
        self.odom_p += np.array([k * (c * step[0] - sn * step[1]), k * (sn * step[0] + c * step[1]), step[2]])
        qb = np.array([0, 0, math.sin(self.odom_yaw_bias / 2), math.cos(self.odom_yaw_bias / 2)])
        q_odom = quat_mul(qb, q)
        sigma = float(self.get_parameter('odom_sigma_frac').value) * self.odom_dist

        tfs = [tf_msg(stamp, 'odom', 'base_link', self.odom_p, q_odom),
               tf_msg(stamp, 'world', 'base_link_truth', p, q)]
        f = self.frames[i]
        Rc = np.array(f['R_cam_from_rover'])            # rover FRD -> rect optical
        R_flu_cam = cahv.FRD_TO_FLU @ Rc.T                # cam -> base_link (FLU)
        t_cam = cahv.FRD_TO_FLU @ np.array(f['C_rover'])
        tfs.append(tf_msg(stamp, 'base_link', 'navcam_left_rect', t_cam, cahv.rot_to_quat(R_flu_cam)))
        tfs.append(tf_msg(stamp, 'base_link_truth', 'navcam_left_rect_truth', t_cam, cahv.rot_to_quat(R_flu_cam)))
        self.tfb.sendTransform(tfs)

        od = Odometry()
        od.header.stamp, od.header.frame_id, od.child_frame_id = stamp, 'odom', 'base_link'
        od.pose.pose.position.x, od.pose.pose.position.y, od.pose.pose.position.z = map(float, self.odom_p)
        (od.pose.pose.orientation.x, od.pose.pose.orientation.y,
         od.pose.pose.orientation.z, od.pose.pose.orientation.w) = map(float, q_odom)
        cov = [0.0] * 36
        cov[0] = cov[7] = max(1e-4, sigma ** 2)
        cov[14] = max(1e-4, (0.5 * sigma) ** 2)
        cov[21] = cov[28] = cov[35] = max(1e-6, (self.odom_yaw_bias * 0.5) ** 2)
        od.pose.covariance = cov
        self.pub_odom.publish(od)

        ps = PoseStamped()
        ps.header.stamp, ps.header.frame_id = stamp, 'world'
        ps.pose.position.x, ps.pose.position.y, ps.pose.position.z = map(float, p)
        ps.pose.orientation.x, ps.pose.orientation.y, ps.pose.orientation.z, ps.pose.orientation.w = map(float, q)
        self.pub_truth.publish(ps)

        lat0, lon0 = self.origin
        fix = NavSatFix()
        fix.header.stamp, fix.header.frame_id = stamp, 'base_link_truth'
        fix.latitude = lat0 + math.degrees(p[1] / MARS_R)
        fix.longitude = lon0 + math.degrees(p[0] / (MARS_R * math.cos(math.radians(lat0))))
        fix.altitude = float(p[2])
        self.pub_fix.publish(fix)

        js = JointState()
        js.header.stamp = stamp
        js.name = ['mast_pan', 'mast_tilt']
        js.position = [math.radians(-f['mast_az']), math.radians(-f['mast_el'])]
        self.pub_js.publish(js)

        if new_frame is not None:
            self.cur = i
            self.publish_frame(f, stamp, sigma)

    def publish_frame(self, f, stamp, sigma):
        for side, pub, pub_i, key in (('left', self.pub_l, self.pub_li, 'P_left'),
                                      ('right', self.pub_r, self.pub_ri, 'P_right')):
            img = cv2.imread(os.path.join(self.dir, f[side]), cv2.IMREAD_COLOR)
            if img is None:
                self.get_logger().warn(f'missing {f[side]}')
                return
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            m = Image()
            m.header.stamp, m.header.frame_id = stamp, 'navcam_left_rect'
            m.height, m.width = img.shape[:2]
            m.encoding, m.step = 'rgb8', img.shape[1] * 3
            m.data = img.tobytes()
            P = np.array(f[key])
            ci = CameraInfo()
            ci.header = m.header
            ci.width, ci.height = m.width, m.height
            ci.distortion_model = 'plumb_bob'
            ci.d = [0.0] * 5
            ci.k = [float(v) for v in P[:, :3].flatten()]
            ci.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
            ci.p = [float(v) for v in P.flatten()]
            pub.publish(m)
            pub_i.publish(ci)
        meta = {k: f.get(k) for k in ('index', 'sol', 'lmst', 'utc', 'site', 'drive', 'mast_az', 'mast_el',
                                      'lat', 'lon', 'image_ids', 'baseline_m', 'georef')}
        meta.update({'frames': len(self.frames), 'odom_sigma_m': sigma, 'left_file': os.path.join(self.dir, f['left']),
                     'stamp_ns': stamp.sec * 1_000_000_000 + stamp.nanosec})
        self.pub_meta.publish(String(data=json.dumps(meta)))
        self.get_logger().info(f"frame {f['index']} sol {f['sol']} site {f['site']} drive {f['drive']} "
                               f"mast az {f['mast_az']:.0f} el {f['mast_el']:.0f}")


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def main():
    rclpy.init()
    node = LogPlayer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
