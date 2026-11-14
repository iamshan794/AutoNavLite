# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
# AutoNav-0 rover environment: ROS 2 Jazzy (LTS) + RTAB-Map (open-source VSLAM) + Rerun bridge.
# Replays Perseverance Navcam stereo logs as if they were produced in real time.
FROM ros:jazzy-ros-base AS deps
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-jazzy-rtabmap-ros \
      ros-jazzy-robot-state-publisher \
      ros-jazzy-cv-bridge \
      ros-jazzy-tf2-ros \
      ros-jazzy-rmw-cyclonedds-cpp \
      python3-opencv python3-numpy python3-scipy python3-requests python3-pip python3-venv \
      curl \
    && rm -rf /var/lib/apt/lists/*
# Rerun lives in its own venv: it pulls NumPy 2, while Ubuntu's OpenCV/cv_bridge need NumPy 1.x.
# --system-site-packages keeps rclpy and the ROS message packages importable.
RUN python3 -m venv --system-site-packages /opt/rerun-venv \
    && /opt/rerun-venv/bin/pip install rerun-sdk==0.43.2
# No usage analytics leave the container (config is image-wide so it applies to any UID).
ENV XDG_CONFIG_HOME=/etc/autonav0
RUN mkdir -p /etc/autonav0 && /opt/rerun-venv/bin/rerun analytics disable && chmod -R a+rX /etc/autonav0

# ---------------------------------------------------------------- learned perception
# PyTorch in its own venv (system NumPy 1.x stays for ROS/OpenCV). TORCH_INDEX selects the build:
#   cu128 (default, NVIDIA incl. Blackwell/RTX 50xx) | https://download.pytorch.org/whl/cpu (small, CPU-only)
FROM deps AS perception
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128
RUN python3 -m venv --system-site-packages /opt/perception-venv \
    && /opt/perception-venv/bin/pip install torch==2.11.0 torchvision==0.26.0 --index-url ${TORCH_INDEX} \
    && /opt/perception-venv/bin/pip install tqdm opt_einsum \
    && /opt/perception-venv/bin/python -c "import numpy; assert numpy.__version__.startswith('1.'), numpy.__version__"
# Pinned, permissively licensed models (code + weights + licences under /opt/models).
ARG RAFT_STEREO_COMMIT=6e93ed2169bd858dbb43033988563f3b0bb49506
ARG XFEAT_COMMIT=e92685f57f8318b18725c5c8c0bd28c7fe188d9a
ENV AUTONAV_MODELS=/opt/models TORCH_HOME=/opt/models/torch
RUN set -eux; mkdir -p /opt/models/raft_stereo /opt/models/xfeat/modules /opt/models/xfeat/weights; cd /tmp; \
    curl -fsSL https://codeload.github.com/princeton-vl/RAFT-Stereo/tar.gz/${RAFT_STEREO_COMMIT} | tar xz; \
    cp -r RAFT-Stereo-${RAFT_STEREO_COMMIT}/core RAFT-Stereo-${RAFT_STEREO_COMMIT}/LICENSE /opt/models/raft_stereo/; \
    curl -fsSL -o models.zip "https://www.dropbox.com/scl/fi/5khx1bhz84dapi8vtwapg/models.zip?rlkey=ggddrn1du1iiq6mgc2dsdpmwi&dl=1"; \
    python3 -c "import zipfile; z=zipfile.ZipFile('models.zip'); [z.extract(n, '/opt/models/raft_stereo') for n in ('raftstereo-eth3d.pth','raftstereo-realtime.pth')]"; \
    X=https://raw.githubusercontent.com/verlab/accelerated_features/${XFEAT_COMMIT}; \
    for f in modules/__init__.py modules/xfeat.py modules/model.py modules/interpolator.py LICENSE weights/xfeat.pt; do \
      curl -fsSL -o /opt/models/xfeat/$f $X/$f; done; \
    /opt/perception-venv/bin/python -c "from torchvision.models.optical_flow import raft_small, Raft_Small_Weights; raft_small(weights=Raft_Small_Weights.DEFAULT)"; \
    rm -rf /tmp/*; chmod -R a+rX /opt/models
# Parquet reader for scripts/terrain_train.py (17.x works with NumPy 1.x; no deps pulled in).
RUN /opt/perception-venv/bin/pip install --no-deps pyarrow==17.0.0

FROM perception AS rover
WORKDIR /ws
COPY src/ src/
RUN . /opt/ros/jazzy/setup.sh && colcon build --event-handlers console_direct- && rm -rf build log
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && useradd -m -u 10001 rover && mkdir -p /data && chown rover /data
USER rover
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    AUTONAV_URL=http://autonav0d:8080 \
    M20_LOG_DIR=/data/m20log
EXPOSE 9090 9876
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "autonav_rover", "rover.launch.py"]
