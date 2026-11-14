#!/usr/bin/env bash
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
# Source ROS, build the Navcam replay log on first start (small download), then run the command.
set -e
source /opt/ros/jazzy/setup.bash
source /ws/install/setup.bash
mkdir -p "${HOME:-/tmp}/.ros" "${ROS_LOG_DIR:-${HOME:-/tmp}/.ros/log}" 2>/dev/null || true
if [[ -z "$(ls "${M20_LOG_DIR}"/*/frames.jsonl 2>/dev/null)" && "${SKIP_LOG_BUILD:-0}" != "1" ]]; then
  echo "[entrypoint] no Navcam log in ${M20_LOG_DIR}; building one (sols ${M20_SOLS:-118:125})"
  for i in $(seq 1 30); do curl -sf "${AUTONAV_URL}/api/health" >/dev/null && break; sleep 2; done
  ros2 run autonav_rover build_m20_log --sols "${M20_SOLS:-118:125}" --max-frames "${M20_MAX_FRAMES:-40}"
fi
exec "$@"
