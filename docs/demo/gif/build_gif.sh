#!/usr/bin/env bash
# Rebuild docs/demo/assets/myia-demo.gif from the real transcripts.
#
# Path A (shipped): terminal-composited frames — make_ass.py turns the real
#   command outputs (transcript/*.txt, see storyboard.md) into frames.ass,
#   ffmpeg rasterizes them on a GitHub-dark canvas. Deterministic, zero GUI,
#   safe to run in CI.
# Path B (optional owner re-record): a real terminal screen capture — see
#   storyboard.md §「给主人的真实屏幕重录指引」; the last ffmpeg line there
#   converts a .mov the same way (fps + palettegen/paletteuse).
#
# Requirements: ffmpeg with libass (Homebrew build has it), Menlo (macOS).
# Usage: bash build_gif.sh    (from this directory)

set -euo pipefail
cd "$(dirname "$0")"

DURATION=42.5   # keep in sync with the last scene end in make_ass.py
FPS=12
SIZE=1100x640
BG=0x0D1117     # GitHub dark canvas
OUT=../assets/myia-demo.gif

python3 make_ass.py

# Two-pass palette: palettegen on the rendered frames, then paletteuse.
# stats_mode=diff biases the palette toward the changing text pixels.
# The palette is a throwaway intermediate — keep it OUT of the repo tree.
PALETTE="$(mktemp -d)/palette.png"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=${BG}:s=${SIZE}:r=${FPS}:d=${DURATION}" \
  -vf "ass=frames.ass,palettegen=stats_mode=diff" \
  -frames:v 1 "${PALETTE}"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=${BG}:s=${SIZE}:r=${FPS}:d=${DURATION}" \
  -i "${PALETTE}" \
  -lavfi "ass=frames.ass [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5" \
  -loop 0 "${OUT}"

echo "wrote ${OUT}: $(du -h "${OUT}" | cut -f1)"
