# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
from glob import glob

from setuptools import setup

package_name = 'autonav_rover'
setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.py')),
        ('share/' + package_name + '/urdf', glob('urdf/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    description='AutoNav-0 rover environment (Navcam replay, RTAB-Map, autonav0d/Rerun bridges)',
    license='MIT',
    tests_require=['pytest'],
    entry_points={'console_scripts': [
        'log_player = autonav_rover.log_player:main',
        'autonav_bridge = autonav_rover.autonav_bridge:main',
        'rerun_bridge = autonav_rover.rerun_bridge:main',
        'build_m20_log = autonav_rover.m20_log_builder:main',
        'stereo_depth = autonav_rover.stereo_depth:main',
        'visual_odometry = autonav_rover.visual_odometry:main',
    ]},
)
