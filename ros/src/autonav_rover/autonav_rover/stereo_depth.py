# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Stereo depth node: rectified Navcam pair -> depth image + coloured point cloud.

Backend: RAFT-Stereo on the GPU when available, SGBM + left-right check on the CPU otherwise
(see perception.py). Publishes
  /rover/depth/image        sensor_msgs/Image 32FC1, metres, NaN = invalid (same stamp/frame as left)
  /rover/stereo_cloud       sensor_msgs/PointCloud2 xyz+rgb in the left rectified camera frame
  /rover/perception/stereo  std_msgs/String JSON {backend, ms, valid_frac, points}
"""
import json
import os

import message_filters
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image, PointCloud2, PointField
from std_msgs.msg import String

from autonav_rover import perception as P


def image_to_rgb(m):
    a = np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.width, -1)
    if m.encoding == 'bgr8':
        a = a[..., ::-1]
    elif a.shape[2] == 1:
        a = np.repeat(a, 3, 2)
    return np.ascontiguousarray(a[..., :3])


def make_cloud(header, xyz, rgb):
    """xyz (N,3) float32 + rgb (N,3) uint8 -> PointCloud2 with packed float rgb (PCL convention)."""
    n = len(xyz)
    packed = (rgb[:, 0].astype(np.uint32) << 16) | (rgb[:, 1].astype(np.uint32) << 8) | rgb[:, 2].astype(np.uint32)
    buf = np.zeros(n, dtype=[('x', 'f4'), ('y', 'f4'), ('z', 'f4'), ('rgb', 'u4')])
    buf['x'], buf['y'], buf['z'], buf['rgb'] = xyz[:, 0], xyz[:, 1], xyz[:, 2], packed
    m = PointCloud2()
    m.header = header
    m.height, m.width = 1, n
    m.fields = [PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
                PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
                PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
                PointField(name='rgb', offset=12, datatype=PointField.FLOAT32, count=1)]
    m.is_bigendian, m.point_step, m.row_step, m.is_dense = False, 16, 16 * n, True
    m.data = buf.tobytes()
    return m


class StereoDepth(Node):
    def __init__(self):
        super().__init__('stereo_depth')
        self.declare_parameter('backend', os.environ.get('AUTONAV_STEREO', 'auto'))   # auto|raft_stereo|sgbm
        self.declare_parameter('device', os.environ.get('AUTONAV_DEVICE', 'auto'))    # auto|cuda|cpu
        self.declare_parameter('variant', os.environ.get('AUTONAV_RAFT_VARIANT', 'eth3d'))  # eth3d|realtime
        self.declare_parameter('max_range_m', 30.0)
        self.declare_parameter('cloud_step', 2)  # pixel subsampling for the cloud
        g = lambda k: self.get_parameter(k).value  # noqa: E731
        self.backend = P.make_stereo(g('backend'), g('device'), variant=g('variant'))
        self.get_logger().info(f'stereo backend: {self.backend.name}')
        self.pub_depth = self.create_publisher(Image, '/rover/depth/image', 5)
        self.pub_cloud = self.create_publisher(PointCloud2, '/rover/stereo_cloud', 5)
        self.pub_status = self.create_publisher(String, '/rover/perception/stereo', 5)
        subs = [message_filters.Subscriber(self, Image, '/rover/navcam/left/image_rect'),
                message_filters.Subscriber(self, Image, '/rover/navcam/right/image_rect'),
                message_filters.Subscriber(self, CameraInfo, '/rover/navcam/left/camera_info'),
                message_filters.Subscriber(self, CameraInfo, '/rover/navcam/right/camera_info')]
        self.sync = message_filters.TimeSynchronizer(subs, 10)
        self.sync.registerCallback(self.on_pair)

    def on_pair(self, li, ri, lc, rc):
        L, R = image_to_rgb(li), image_to_rgb(ri)
        Pl, Pr = np.array(lc.p).reshape(3, 4), np.array(rc.p).reshape(3, 4)
        fx = Pl[0, 0]
        baseline = -Pr[0, 3] / Pr[0, 0] if Pr[0, 0] else 0.0
        if baseline <= 0:
            self.get_logger().warn('right camera_info has no baseline (P[0,3]); skipping')
            return
        with P.Timer() as tm:
            disp = self.backend.disparity(L, R)
        depth = P.disparity_to_depth(disp, fx, baseline, zmax=float(self.get_parameter('max_range_m').value))
        d = Image()
        d.header = li.header
        d.height, d.width, d.encoding, d.is_bigendian = depth.shape[0], depth.shape[1], '32FC1', 0
        d.step = depth.shape[1] * 4
        d.data = depth.tobytes()
        self.pub_depth.publish(d)
        xyz, (v, u) = P.backproject(depth, Pl[:, :3], step=int(self.get_parameter('cloud_step').value))
        self.pub_cloud.publish(make_cloud(li.header, xyz, L[v, u]))
        valid = float(np.isfinite(depth).mean())
        self.pub_status.publish(String(data=json.dumps(
            {'backend': self.backend.name, 'ms': round(tm.ms, 1), 'valid_frac': round(valid, 3),
             'points': int(len(xyz)), 'baseline_m': round(float(baseline), 4)})))
        self.get_logger().info(f'{self.backend.name}: {tm.ms:.0f} ms, valid {valid * 100:.0f}%, {len(xyz)} pts')


def main():
    rclpy.init()
    node = StereoDepth()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
