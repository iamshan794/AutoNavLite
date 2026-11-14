# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Keyframe visual odometry / re-localisation (learned features or optical flow + depth + PnP).

For each new left image (+ its stereo depth):
  1. predict the camera pose from wheel odometry (TF odom->navcam_left_rect),
  2. match it against nearby keyframes - XFeat (GPU/CPU) or ORB descriptors, or RAFT/LK flow -
     and solve PnP+RANSAC on keyframe 3D points (keyframe depth) vs current 2D points,
  3. best match wins: pose = keyframe pose ∘ relative pose; otherwise keep the odometry prediction.
With Navcam's sparse, non-overlapping drive frames this behaves like re-localisation: drift accumulates
between stops and collapses when the rover sees a place it mapped before.

Publishes /rover/vo/camera_pose, /rover/vo/odom (base_link, 'odom' frame) and /rover/vo/status (JSON).
"""
import json
import math
import os

import cv2
import message_filters
import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

from autonav_rover import perception as P
from autonav_rover.stereo_depth import image_to_rgb


def tf_to_mat(tr):
    t, q = tr.transform.translation, tr.transform.rotation
    x, y, z, w = q.x, q.y, q.z, q.w
    M = np.eye(4)
    M[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    M[:3, 3] = [t.x, t.y, t.z]
    return M


def mat_to_quat(R):
    from autonav_rover.cahv import rot_to_quat
    return rot_to_quat(R)


class VisualOdometry(Node):
    def __init__(self):
        super().__init__('visual_odometry')
        self.declare_parameter('device', os.environ.get('AUTONAV_DEVICE', 'auto'))
        self.declare_parameter('method', os.environ.get('AUTONAV_VO_METHOD', 'features'))  # features|flow
        self.declare_parameter('features', os.environ.get('AUTONAV_FEATURES', 'auto'))    # auto|xfeat|orb
        self.declare_parameter('flow', os.environ.get('AUTONAV_FLOW', 'auto'))            # auto|raft|lk
        self.declare_parameter('min_inliers', 30)
        self.declare_parameter('max_keyframes', 200)
        self.declare_parameter('search_radius_m', 30.0)
        g = lambda k: self.get_parameter(k).value  # noqa: E731
        self.method = g('method')
        self.feat = P.make_features(g('features'), g('device')) if self.method == 'features' else None
        self.flow = P.make_flow(g('flow'), g('device')) if self.method == 'flow' else None
        self.backend = (self.feat or self.flow).name
        self.get_logger().info(f'VO backend: {self.backend} ({self.method})')
        self.kfs = []
        self.last = None  # (T_est, T_odom) of the previous frame
        self.tf = Buffer()
        self.tfl = TransformListener(self.tf, self)
        self.pub_pose = self.create_publisher(PoseStamped, '/rover/vo/camera_pose', 10)
        self.pub_odom = self.create_publisher(Odometry, '/rover/vo/odom', 10)
        self.pub_status = self.create_publisher(String, '/rover/vo/status', 10)
        subs = [message_filters.Subscriber(self, Image, '/rover/navcam/left/image_rect'),
                message_filters.Subscriber(self, Image, '/rover/depth/image'),
                message_filters.Subscriber(self, CameraInfo, '/rover/navcam/left/camera_info')]
        self.sync = message_filters.TimeSynchronizer(subs, 10)
        self.sync.registerCallback(self.on_frame)

    def lookup(self, parent, child, stamp):
        try:
            return tf_to_mat(self.tf.lookup_transform(parent, child, Time.from_msg(stamp),
                                                      timeout=rclpy.duration.Duration(seconds=0.5)))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ correspondences
    def correspondences(self, kf, cur):
        """-> (kf pixel coords (N,2), current pixel coords (N,2))."""
        if self.feat:
            m = self.feat.match(kf['desc'], cur['desc'])
            if len(m) == 0:
                return np.zeros((0, 2)), np.zeros((0, 2))
            return kf['kpts'][m[:, 0]], cur['kpts'][m[:, 1]]
        return self.flow.track(kf['rgb'], cur['rgb'], kf['kpts'])

    def relative_pose(self, kf, cur, K):
        p_kf, p_cur = self.correspondences(kf, cur)
        if len(p_kf) < 8:
            return None, len(p_kf), 0, None
        u = np.clip(np.round(p_kf[:, 0]).astype(int), 0, kf['depth'].shape[1] - 1)
        v = np.clip(np.round(p_kf[:, 1]).astype(int), 0, kf['depth'].shape[0] - 1)
        z = kf['depth'][v, u]
        ok = np.isfinite(z) & (z < 25.0)
        if ok.sum() < 8:
            return None, len(p_kf), 0, None
        X = np.stack([(p_kf[ok, 0] - K[0, 2]) * z[ok] / K[0, 0], (p_kf[ok, 1] - K[1, 2]) * z[ok] / K[1, 1], z[ok]], 1)
        found, rvec, tvec, inl = cv2.solvePnPRansac(X.astype(np.float64), p_cur[ok].astype(np.float64), K, None,
                                                    iterationsCount=300, reprojectionError=2.0, confidence=0.999,
                                                    flags=cv2.SOLVEPNP_AP3P)
        if not found or inl is None:
            return None, len(p_kf), 0, None
        inl = inl.ravel()
        # Refine on inliers.
        rvec, tvec = cv2.solvePnPRefineLM(X[inl], p_cur[ok][inl].astype(np.float64), K, None, rvec, tvec)
        T = np.eye(4)  # T_cur<-kf
        T[:3, :3], T[:3, 3] = cv2.Rodrigues(rvec)[0], tvec.ravel()
        return T, len(p_kf), len(inl), p_cur[ok][inl]

    # ------------------------------------------------------------------ main callback
    def on_frame(self, img, depth_msg, info):
        K = np.array(info.k).reshape(3, 3)
        rgb = image_to_rgb(img)
        depth = np.frombuffer(bytes(depth_msg.data), np.float32).reshape(depth_msg.height, depth_msg.width)
        T_odom = self.lookup('odom', 'navcam_left_rect', img.header.stamp)
        T_truth = self.lookup('world', 'navcam_left_rect_truth', img.header.stamp)
        if T_odom is None:
            self.get_logger().warn('no odom->camera TF yet')
            return
        with P.Timer() as tm:
            cur = {'rgb': rgb, 'depth': depth, 'stamp': img.header.stamp}
            if self.feat:
                cur['kpts'], cur['desc'] = self.feat.detect(rgb)
            else:
                g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
                c = cv2.goodFeaturesToTrack(g, 3000, 0.01, 7)
                cur['kpts'] = c.reshape(-1, 2).astype(np.float32) if c is not None else np.zeros((0, 2), np.float32)
            # 1) odometry prediction
            T_pred = T_odom if self.last is None else self.last[0] @ np.linalg.inv(self.last[1]) @ T_odom
            # 2) candidate keyframes near the prediction
            r = float(self.get_parameter('search_radius_m').value)
            cands = sorted(self.kfs, key=lambda k: np.linalg.norm(k['T'][:3, 3] - T_pred[:3, 3]))
            cands = [k for k in cands if np.linalg.norm(k['T'][:3, 3] - T_pred[:3, 3]) < r][:8]
            best = None
            for kf in cands:
                T_rel, n_match, n_inl, pts = self.relative_pose(kf, cur, K)
                if T_rel is not None and (best is None or n_inl > best[2]):
                    best = (kf, T_rel, n_inl, n_match, pts)
            min_inl = int(self.get_parameter('min_inliers').value)
            if best and best[2] >= min_inl:
                kf, T_rel, n_inl, n_match, pts = best
                T_est = kf['T'] @ np.linalg.inv(T_rel)
                method = f"pnp:kf{kf['id']}"
            else:
                T_est, n_inl, n_match, pts = T_pred, (best[2] if best else 0), (best[3] if best else 0), None
                method = 'odom-prediction'
            # 3) keyframe insertion (skip near-duplicates of a well-matched keyframe)
            dup = best and best[2] >= min_inl and np.linalg.norm(np.linalg.inv(best[1])[:3, 3]) < 0.05 and \
                best[2] > 0.5 * len(cur['kpts'])
            if not dup:
                cur.update(T=T_est, id=len(self.kfs))
                self.kfs.append(cur)
                if len(self.kfs) > int(self.get_parameter('max_keyframes').value):
                    self.kfs.pop(0)
        self.last = (T_est, T_odom)

        stamp = img.header.stamp
        ps = PoseStamped()
        ps.header.stamp, ps.header.frame_id = stamp, 'odom'
        ps.pose.position.x, ps.pose.position.y, ps.pose.position.z = map(float, T_est[:3, 3])
        (ps.pose.orientation.x, ps.pose.orientation.y, ps.pose.orientation.z,
         ps.pose.orientation.w) = map(float, mat_to_quat(T_est[:3, :3]))
        self.pub_pose.publish(ps)
        T_cam_base = self.lookup('navcam_left_rect', 'base_link', stamp)
        if T_cam_base is not None:
            Tb = T_est @ T_cam_base
            od = Odometry()
            od.header.stamp, od.header.frame_id, od.child_frame_id = stamp, 'odom', 'base_link'
            od.pose.pose.position.x, od.pose.pose.position.y, od.pose.pose.position.z = map(float, Tb[:3, 3])
            (od.pose.pose.orientation.x, od.pose.pose.orientation.y, od.pose.pose.orientation.z,
             od.pose.pose.orientation.w) = map(float, mat_to_quat(Tb[:3, :3]))
            self.pub_odom.publish(od)
        st = {'backend': self.backend, 'method': method, 'matches': int(n_match), 'inliers': int(n_inl),
              'keyframes': len(self.kfs), 'ms': round(tm.ms, 1), 'keypoints': int(len(cur['kpts']))}
        if T_truth is not None:
            st['vo_error_m'] = round(float(np.linalg.norm(T_est[:3, 3] - T_truth[:3, 3])), 3)
            st['odom_error_m'] = round(float(np.linalg.norm(T_odom[:3, 3] - T_truth[:3, 3])), 3)
            ang = np.clip((np.trace(T_est[:3, :3].T @ T_truth[:3, :3]) - 1) / 2, -1, 1)
            st['vo_heading_error_deg'] = round(math.degrees(math.acos(ang)), 2)
        if pts is not None:
            st['inlier_px'] = np.round(pts[:400], 1).tolist()
        self.pub_status.publish(String(data=json.dumps(st)))
        self.get_logger().info(f"{method:16s} inl {n_inl:4d}/{n_match:4d} kf {len(self.kfs):3d} {tm.ms:6.0f} ms "
                               f"err vo {st.get('vo_error_m', '?')} m vs odom {st.get('odom_error_m', '?')} m")


def main():
    rclpy.init()
    node = VisualOdometry()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
