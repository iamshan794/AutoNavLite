#!/usr/bin/env bash
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
# AutoNav-0 stack helper: preflight checks + docker compose.
#   scripts/stack.sh up [--rover] [--rerun]
#                                     start map service (+ ROS 2 rover twin, RTAB-Map)
#                                     --rerun: dev-only Rerun 3D debug viewer (not for production)
#   scripts/stack.sh down             stop everything
#   scripts/stack.sh status           containers, URLs, port owners
# Env overrides: AUTONAV_PORT (8080), RERUN_WEB_PORT (9090), RERUN_GRPC_PORT (9876), M20_SOLS, FRAME_PERIOD_S
set -euo pipefail
cd "$(dirname "$0")/.."
export UID GID="${GID:-$(id -g)}"
AUTONAV_PORT="${AUTONAV_PORT:-8080}" RERUN_WEB_PORT="${RERUN_WEB_PORT:-9090}" RERUN_GRPC_PORT="${RERUN_GRPC_PORT:-9876}"
export AUTONAV_PORT RERUN_WEB_PORT RERUN_GRPC_PORT

# Port owned by something other than this compose project? Print the owner and fail.
check_port() {
  local port=$1 name=$2
  local line
  line=$(ss -Hltnp "sport = :$port" 2>/dev/null | head -1) || true
  [[ -z "$line" ]] && return 0
  if docker compose ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$port->"; then return 0; fi
  local owner
  owner=$(grep -oE 'users:\(\("[^"]+",pid=[0-9]+' <<<"$line" | sed -E 's/users:\(\("([^"]+)",pid=([0-9]+)/\1 (pid \2)/' || true)
  echo "✗ port $port ($name) is already in use by ${owner:-another process (possibly another container)}." >&2
  if [[ "$owner" == autonav0d* ]]; then
    local pid=${owner##*pid }; pid=${pid%)}
    {
      echo "  It's a manually started autonav0d. Stop it (Ctrl+C in its terminal, or: kill $pid)"
      echo "  or pick another port:  AUTONAV_PORT=8081 $0 up ${ROVER:+--rover}"
    } >&2
  else
    echo "  Free the port or choose another one, e.g.  ${3}=$((port + 1)) $0 up ${ROVER:+--rover}" >&2
  fi
  return 1
}

cmd="${1:-status}"; shift || true
ROVER=""; RERUN=""
for a in "$@"; do
  case "$a" in
    --rover) ROVER=1 ;;
    --rerun) RERUN=1; ROVER=1 ;;  # the viewer only shows rover data
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
PROFILES=(); [[ -n "$ROVER" ]] && PROFILES+=(--profile rover)
if [[ -n "$RERUN" ]]; then
  PROFILES+=(--profile rerun)
  export AUTONAV_RERUN=true
  export AUTONAV_RERUN_URL="http://{host}:$RERUN_WEB_PORT/?url=rerun%2Bhttp%3A%2F%2F{host}%3A$RERUN_GRPC_PORT%2Fproxy"
fi
# Use the GPU override when an NVIDIA GPU is usable from Docker (else CPU perception fallbacks).
FILES=(-f compose.yaml)
if [[ -n "$ROVER" && "${AUTONAV_DEVICE:-auto}" != "cpu" ]] && command -v nvidia-smi >/dev/null 2>&1 \
   && nvidia-smi -L >/dev/null 2>&1 && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  FILES+=(-f compose.gpu.yaml); echo "• NVIDIA GPU detected: learned perception on GPU"
elif [[ -n "$ROVER" ]]; then
  echo "• no usable NVIDIA GPU: perception uses CPU fallbacks (SGBM, XFeat-CPU/ORB, LK)"
fi

case "$cmd" in
  up)
    ok=1
    check_port "$AUTONAV_PORT" "map service" AUTONAV_PORT || ok=0
    if [[ -n "$RERUN" ]]; then
      check_port "$RERUN_WEB_PORT" "Rerun web viewer" RERUN_WEB_PORT || ok=0
      check_port "$RERUN_GRPC_PORT" "Rerun gRPC" RERUN_GRPC_PORT || ok=0
    fi
    [[ $ok == 1 ]] || exit 1
    # Create bind-mount dirs as the current user (Docker would create them root-owned).
    mkdir -p data/m20log
    if [[ ! -f data/catalog/layers.json ]]; then
      echo "• fetching offline data pack (first run)…"
      docker compose --profile setup run --rm fetch-data
    fi
    docker compose "${FILES[@]}" "${PROFILES[@]}" up -d --build
    echo
    echo "✓ map:   http://localhost:$AUTONAV_PORT"
    [[ -n "$ROVER" ]] && echo "✓ rover: live position on the map (ROS 2 + RTAB-Map)"
    [[ -n "$RERUN" ]] && echo "✓ rerun (dev): http://localhost:$RERUN_WEB_PORT/?url=rerun%2Bhttp%3A%2F%2Flocalhost%3A$RERUN_GRPC_PORT%2Fproxy"
    ;;
  down)
    docker compose --profile rover --profile rerun --profile setup down
    ;;
  status)
    docker compose --profile rover --profile rerun ps
    for p in "$AUTONAV_PORT" "$RERUN_WEB_PORT" "$RERUN_GRPC_PORT"; do
      ss -Hltnp "sport = :$p" 2>/dev/null | sed "s/^/port $p: /" | head -1 || true
    done
    ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
