#!/usr/bin/env bash
# Generate deterministic synthetic fixtures with ffmpeg lavfi:
# color bars + sine tone + burned-in frame counter. Tiny, reproducible, no network.
set -euo pipefail

OUT="${1:-fixtures/synth}"
mkdir -p "$OUT"

# Resolve a Latin font for the burned-in frame counter (CI has DejaVu; this box has Liberation).
FONT="$(fc-match -f "%{file}\n" "DejaVu Sans" 2>/dev/null || true)"
if [[ -z "$FONT" || ! -f "$FONT" ]]; then
  FONT="$(fc-match -f "%{file}\n" sans 2>/dev/null)"
fi
echo "[fixtures] font: $FONT"

gen() {
  local name="$1" dur="$2"; shift 2
  echo "[fixtures] $name (${dur}s)"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=720x1280:rate=30:duration=$dur" \
    -f lavfi -i "sine=frequency=440:duration=$dur" \
    "$@" \
    -vf "drawtext=fontfile=$FONT:text='%{frame_num}':x=20:y=20:fontsize=48:fontcolor=white,format=yuv420p" \
    -c:v libx264 -preset veryfast -crf 23 -r 30 \
    -c:a aac -ar 48000 -ac 2 \
    -t "$dur" "$OUT/$name.mp4"
}

gen bars_5s 5
gen bars_30s 30
gen bars_89s 89
gen bars_91s 91

echo "[fixtures] vfr (variable frame rate: periodic frame gaps keep original PTS)"
ffmpeg -hide_banner -loglevel error -y \
  -i "$OUT/bars_5s.mp4" \
  -vf "select='lt(mod(n,30),20)',format=yuv420p" \
  -fps_mode vfr \
  -c:v libx264 -preset veryfast -crf 23 \
  -c:a copy \
  "$OUT/vfr.mp4"

echo "[fixtures] rotated (landscape pixels; NOTE: ffmpeg 9 CLI cannot write mp4 rotate/displaymatrix metadata,"
echo "  so true rotation-metadata coverage uses synthetic ffprobe JSON in Phase 2 unit tests + a real phone clip in fixtures/real/)"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=5" \
  -f lavfi -i "sine=frequency=440:duration=5" \
  -vf "drawtext=fontfile=$FONT:text='%{frame_num}':x=20:y=20:fontsize=48:fontcolor=white,format=yuv420p" \
  -c:v libx264 -preset veryfast -crf 23 -r 30 \
  -c:a aac -ar 48000 -ac 2 \
  "$OUT/rotated.mp4"

echo "[fixtures] done: $(ls "$OUT"/*.mp4 | wc -l) files"
