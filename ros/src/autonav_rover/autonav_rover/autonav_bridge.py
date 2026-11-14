# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""ROS 2 <-> autonav0d bridge (HTTP/JSON today; a protobuf/gRPC transport can replace it later).

* Pushes the rover estimate to autonav0d (POST /api/rover/state) so the 2D/3D map shows it live.
  Estimate = map->base_link from RTAB-Map (VSLAM-corrected) when available, else wheel odometry.
* /autonav/goal (sensor_msgs/NavSatFix) -> GET /api/route -> /autonav/path (nav_msgs/Path, map frame)
  and /autonav/route_geojson (std_msgs/String).
"""
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request

import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry, Path
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from rclpy.time import Time
from sensor_msgs.msg import NavSatFix
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

MARS_R = 3396190.0


class Bridge(Node):
    def __init__(self):
        super().__init__('autonav_bridge')
        self.declare_parameter('autonav_url', os.environ.get('AUTONAV_URL', 'http://localhost:8080'))
        self.declare_parameter('rate_hz', 2.0)
        self.declare_parameter('route_profile', 'wear')
        self.declare_parameter('rover_name', 'Perseverance (Navcam replay)')
        self.url = self.get_parameter('autonav_url').value.rstrip('/')
        self.origin = None
        self.meta = {}
        self.odom_sigma = 0.0
        self.truth = None
        self.last_err = None
        self.tf = Buffer()
        self.tfl = TransformListener(self.tf, self)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(NavSatFix, '/rover/map_origin', self.on_origin, latched)
        self.create_subscription(String, '/rover/frame_meta', lambda m: setattr(self, 'meta', json.loads(m.data)), 10)
        self.create_subscription(Odometry, '/rover/wheel_odom', self.on_odom, 10)
        self.create_subscription(NavSatFix, '/rover/fix', lambda m: setattr(self, 'truth', (m.latitude, m.longitude)), 10)
        self.create_subscription(NavSatFix, '/autonav/goal', self.on_goal, 10)
        self.pub_path = self.create_publisher(Path, '/autonav/path', latched)
        self.pub_route = self.create_publisher(String, '/autonav/route_geojson', latched)
        self.create_timer(1.0 / float(self.get_parameter('rate_hz').value), self.push)
        self.get_logger().info(f'bridging to {self.url}')

    def on_origin(self, m):
        self.origin = (m.latitude, m.longitude)

    def on_odom(self, m):
        self.odom_sigma = math.sqrt(max(0.0, m.pose.covariance[0]))

    # ------------------------------------------------------------ geo helpers
    def to_latlon(self, x, y):
        lat0, lon0 = self.origin
        return (lat0 + math.degrees(y / MARS_R),
                lon0 + math.degrees(x / (MARS_R * math.cos(math.radians(lat0)))))

    def to_xy(self, lat, lon):
        lat0, lon0 = self.origin
        return (math.radians(lon - lon0) * MARS_R * math.cos(math.radians(lat0)),
                math.radians(lat - lat0) * MARS_R)

    def http(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    # ------------------------------------------------------------ rover state -> autonav0d
    def estimate(self):
        """(x, y, z, yaw, source) of base_link in map: SLAM if map->odom exists, else odom only."""
        for parent, src in (('map', 'rtabmap'), ('odom', 'wheel_odom')):
            try:
                t = self.tf.lookup_transform(parent, 'base_link', Time())
            except Exception:  # noqa: BLE001
                continue
            tr, q = t.transform.translation, t.transform.rotation
            yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
            return tr.x, tr.y, tr.z, yaw, src
        return None

    def push(self):
        if not self.origin:
            return
        est = self.estimate()
        if not est:
            return
        x, y, z, yaw, src = est
        lat, lon = self.to_latlon(x, y)
        body = {'name': self.get_parameter('rover_name').value, 'lat': lat, 'lon': lon,
                'heading_deg': (90.0 - math.degrees(yaw)) % 360.0,  # ENU yaw -> compass
                'sigma_m': round(self.odom_sigma if src == 'wheel_odom' else min(self.odom_sigma, 5.0), 2),
                'source': src, 'sol': self.meta.get('sol'), 'site': self.meta.get('site'),
                'drive': self.meta.get('drive'), 'lmst': self.meta.get('lmst')}
        if self.truth:
            body['truth_lat'], body['truth_lon'] = self.truth
            tx, ty = self.to_xy(*self.truth)
            body['error_m'] = round(math.hypot(tx - x, ty - y), 2)
            try:  # dead-reckoning-only error, to show what VSLAM buys us
                o = self.tf.lookup_transform('odom', 'base_link', Time()).transform.translation
                body['odom_error_m'] = round(math.hypot(tx - o.x, ty - o.y), 2)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.http('POST', '/api/rover/state', body)
            if self.last_err:
                self.get_logger().info('autonav0d reachable again')
            self.last_err = None
        except (urllib.error.URLError, OSError) as e:
            if str(e) != self.last_err:
                self.get_logger().warn(f'autonav0d unreachable: {e}')
            self.last_err = str(e)

    # ------------------------------------------------------------ goal -> route
    def on_goal(self, goal):
        est = self.estimate()
        if not (est and self.origin):
            self.get_logger().warn('no rover pose yet; cannot plan')
            return
        lat, lon = self.to_latlon(est[0], est[1])
        q = urllib.parse.urlencode({'from': f'{lat},{lon}', 'to': f'{goal.latitude},{goal.longitude}',
                                    'profile': self.get_parameter('route_profile').value})
        try:
            gj = self.http('GET', '/api/route?' + q)
        except Exception as e:  # noqa: BLE001
            self.get_logger().error(f'route failed: {e}')
            return
        path = Path()
        path.header.frame_id = 'map'
        path.header.stamp = self.get_clock().now().to_msg()
        for lo, la, el in gj['geometry']['coordinates']:
            ps = PoseStamped()
            ps.header = path.header
            ps.pose.position.x, ps.pose.position.y = self.to_xy(la, lo)
            ps.pose.orientation.w = 1.0
            path.poses.append(ps)
        self.pub_path.publish(path)
        self.pub_route.publish(String(data=json.dumps(gj)))
        p = gj['properties']
        self.get_logger().info(f"route {p['profile']}: {p['distance_m']:.0f} m, wear {p['wear_index']}")


def main():
    rclpy.init()
    node = Bridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
