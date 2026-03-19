#!/usr/bin/env bash
# REEcord wrapper — fixes Windows path issue with MINGW64 + Docker

set -euo pipefail

# Use Windows-style path so Docker Desktop mounts correctly
# Converts $HOME (/c/Users/USERNAME) to Windows-style (C:/Users/USERNAME)
WIN_CACHE="$(echo "$USERPROFILE" | tr '\\' '/')/.cache"
mkdir -p "$WIN_CACHE/gensyn" "$WIN_CACHE/huggingface"

MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${WIN_CACHE}:/home/gensyn/.cache" \
  gensynai/ree:v0.1.0 \
  run-all \
  --tasks-root /home/gensyn/.cache/gensyn \
  "$@"

echo ""
echo "=== Receipt ==="
find "$WIN_CACHE/gensyn" -name "receipt_*.json" | sort | tail -1
