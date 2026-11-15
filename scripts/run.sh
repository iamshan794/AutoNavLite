#!/usr/bin/env bash
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
# Build (if needed), fetch the small offline data pack (if needed) and run AutoNav-0 on localhost:
#   scripts/run.sh               entry screen on http://127.0.0.1:8000 (autonav_gate); Enter starts the
#                                map app on :8080, Exit stops it again
#   scripts/run.sh --app-only [autonav0d options]   the map app alone on :8080 (no entry screen)
# Env: GATE_PORT (8000), AUTONAV_PORT (8080), HOST (127.0.0.1; use 0.0.0.0 to serve other machines).
set -euo pipefail
cd "$(dirname "$0")/.."
GATE_PORT="${GATE_PORT:-8000}" AUTONAV_PORT="${AUTONAV_PORT:-8080}" HOST="${HOST:-127.0.0.1}"

if [[ ! -x build/autonav0d || ! -x build/autonav_gate ]]; then
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
  cmake --build build -j"$(nproc)"
fi
if [[ ! -f data/catalog/layers.json || -z "$(ls data/dem/*.img 2>/dev/null)" ]]; then
  python3 scripts/fetch_data.py
fi
if [[ "${1:-}" == "--app-only" ]]; then
  shift
  exec ./build/autonav0d --host "$HOST" --port "$AUTONAV_PORT" --data data --web web "$@"
fi
exec ./build/autonav_gate --host "$HOST" --port "$GATE_PORT" --web web/entry \
  --app-url "http://127.0.0.1:$AUTONAV_PORT" --public-app-url "http://{host}:$AUTONAV_PORT/" \
  --app-cmd "exec ./build/autonav0d --host $HOST --port $AUTONAV_PORT --data data --web web --gateway-url http://{host}:$GATE_PORT"
