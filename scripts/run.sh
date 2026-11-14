#!/usr/bin/env bash
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
# Build (if needed), fetch the small offline data pack (if needed) and run autonav0d.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x build/autonav0d ]]; then
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
  cmake --build build -j"$(nproc)"
fi
if [[ ! -f data/catalog/layers.json || -z "$(ls data/dem/*.img 2>/dev/null)" ]]; then
  python3 scripts/fetch_data.py
fi
exec ./build/autonav0d --data data --web web "$@"
