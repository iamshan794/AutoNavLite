# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""AutoNav-0 rover: Navcam log replay + RTAB-Map stereo VSLAM + autonav0d bridge + Rerun bridge."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


# Perception runs in its own venv (PyTorch); falls back to the system python (CPU backends only).
PY = '/opt/perception-venv/bin/python' if os.path.exists('/opt/perception-venv/bin/python') else 'python3'


def generate_launch_description():
    share = get_package_share_directory('autonav_rover')
    urdf = open(os.path.join(share, 'urdf', 'rover.urdf')).read()
    use_rerun, use_slam = LaunchConfiguration('rerun'), LaunchConfiguration('slam')
    stereo = [('left/image_rect', '/rover/navcam/left/image_rect'),
              ('right/image_rect', '/rover/navcam/right/image_rect'),
              ('left/image', '/rover/navcam/left/image_rect'),
              ('right/image', '/rover/navcam/right/image_rect'),
              ('left/camera_info', '/rover/navcam/left/camera_info'),
              ('right/camera_info', '/rover/navcam/right/camera_info')]
    return LaunchDescription([
        DeclareLaunchArgument('rerun', default_value=os.environ.get('AUTONAV_RERUN', 'false'),
                              description='stream to a Rerun viewer (dev only; needs the rerun service)'),
        DeclareLaunchArgument('slam', default_value='true'),
        DeclareLaunchArgument('frame_period_s', default_value='3.0'),
        DeclareLaunchArgument('stereo', default_value=os.environ.get('AUTONAV_STEREO', 'auto'),
                              description='auto | raft_stereo | sgbm'),
        DeclareLaunchArgument('device', default_value=os.environ.get('AUTONAV_DEVICE', 'auto'),
                              description='auto | cuda | cpu'),
        DeclareLaunchArgument('vo_method', default_value=os.environ.get('AUTONAV_VO_METHOD', 'features'),
                              description='features (XFeat/ORB) | flow (RAFT/LK)'),
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             parameters=[{'robot_description': urdf}]),
        Node(package='autonav_rover', executable='log_player', output='screen',
             parameters=[{'frame_period_s': LaunchConfiguration('frame_period_s')}]),
        # Learned perception (GPU when available, CPU fallback): stereo depth + cloud, keyframe VO.
        ExecuteProcess(output='screen', cmd=[PY, '-m', 'autonav_rover.stereo_depth', '--ros-args',
                                             '-p', ['backend:=', LaunchConfiguration('stereo')],
                                             '-p', ['device:=', LaunchConfiguration('device')]]),
        ExecuteProcess(output='screen', cmd=[PY, '-m', 'autonav_rover.visual_odometry', '--ros-args',
                                             '-p', ['method:=', LaunchConfiguration('vo_method')],
                                             '-p', ['device:=', LaunchConfiguration('device')]]),
        # Open-source VSLAM: stereo features + wheel odometry (TF odom->base_link) + loop closure.
        Node(package='rtabmap_slam', executable='rtabmap', namespace='rtabmap', output='screen',
             condition=IfCondition(use_slam), arguments=['-d'],
             parameters=[{
                 'frame_id': 'base_link', 'odom_frame_id': 'odom', 'map_frame_id': 'map',
                 'subscribe_stereo': True, 'subscribe_depth': False, 'subscribe_rgb': False,
                 'approx_sync': False, 'publish_tf': True, 'wait_for_transform': 0.5,
                 'database_path': '/tmp/rtabmap.db',
                 # Odometry comes from TF (no covariance): give it realistic per-update uncertainty so
                 # loop closures can redistribute accumulated drift.
                 'odom_tf_linear_variance': 0.05, 'odom_tf_angular_variance': 0.005,
                 'Rtabmap/DetectionRate': '0', 'RGBD/LinearUpdate': '0', 'RGBD/AngularUpdate': '0',
                 'RGBD/ProximityBySpace': 'true', 'RGBD/OptimizeMaxError': '3.0',
                 'Mem/IncrementalMemory': 'true',
                 # Bounded memory: no raw images in the database, cap working memory; the log player
                 # also switches to localisation mode after the first pass.
                 'Mem/BinDataKept': 'false', 'Mem/NotLinkedNodesKept': 'false', 'Rtabmap/MemoryThr': '300', 'Vis/MinInliers': '12', 'Kp/MaxFeatures': '600',
                 'Vis/MaxFeatures': '1200', 'Stereo/MaxDisparity': '256', 'Grid/RangeMax': '25',
             }],
             remappings=stereo),
        Node(package='autonav_rover', executable='autonav_bridge', output='screen'),
        ExecuteProcess(condition=IfCondition(use_rerun), output='screen',
                       cmd=['/opt/rerun-venv/bin/python', '-m', 'autonav_rover.rerun_bridge']),
    ])
