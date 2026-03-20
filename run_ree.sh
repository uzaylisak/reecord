#!/usr/bin/env bash
# REEcord — cross-platform REE runner (Windows / Linux / macOS)

set -euo pipefail

case "$(uname -s)" in
  Linux*|Darwin*)
    # Linux / macOS — use $HOME directly
    CACHE_DIR="$HOME/.cache"
    mkdir -p "$CACHE_DIR/gensyn" "$CACHE_DIR/huggingface"

    docker run --rm \
      -v "${CACHE_DIR}:/home/gensyn/.cache" \
      gensynai/ree:v0.1.0 \
      run-all \
      --tasks-root /home/gensyn/.cache/gensyn \
      "$@"
    ;;

  MINGW*|CYGWIN*|MSYS*)
    # Windows (Git Bash / MSYS2) — convert path for Docker Desktop
    WIN_CACHE="$(echo "$USERPROFILE" | tr '\\' '/')/.cache"
    mkdir -p "$WIN_CACHE/gensyn" "$WIN_CACHE/huggingface"

    MSYS_NO_PATHCONV=1 docker run --rm \
      -v "${WIN_CACHE}:/home/gensyn/.cache" \
      gensynai/ree:v0.1.0 \
      run-all \
      --tasks-root /home/gensyn/.cache/gensyn \
      "$@"

    CACHE_DIR="$WIN_CACHE"
    ;;

  *)
    echo "Unsupported OS: $(uname -s)"
    exit 1
    ;;
esac

echo ""
echo "=== Receipt ==="
find "$CACHE_DIR/gensyn" -name "receipt_*.json" | sort | tail -1
