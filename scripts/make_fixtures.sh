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

echo "[fixtures] silent_5s (no audio stream)"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=720x1280:rate=30:duration=5" \
  -vf "drawtext=fontfile=$FONT:text='%{frame_num}':x=20:y=20:fontsize=48:fontcolor=white,format=yuv420p" \
  -c:v libx264 -preset veryfast -crf 23 -r 30 -an \
  -t 5 "$OUT/silent_5s.mp4"

echo "[fixtures] mono_5s (single channel audio)"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=720x1280:rate=30:duration=5" \
  -f lavfi -i "sine=frequency=440:duration=5" \
  -vf "drawtext=fontfile=$FONT:text='%{frame_num}':x=20:y=20:fontsize=48:fontcolor=white,format=yuv420p" \
  -c:v libx264 -preset veryfast -crf 23 -r 30 \
  -c:a aac -ar 48000 -ac 1 \
  -t 5 "$OUT/mono_5s.mp4"

echo "[fixtures] done: $(ls "$OUT"/*.mp4 | wc -l) files total"

for spec in "red:card_red" "blue:card_blue" "green:card_green"; do
  color="${spec%%:*}"; name="${spec##*:}"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "color=c=$color:s=1080x1920:d=1" \
    -frames:v 1 "$OUT/$name.png"
done
echo "[fixtures] overlay cards: $(ls "$OUT"/*.png | wc -l) files"

# Real speech (network, optional: SPEECH=1). CC0 recordings from Freesound via Openverse:
#   speech_en: "Narration Request"            https://freesound.org/s/381993/ (CC0)
#   speech_pt: "audio of a man philosophizing" https://freesound.org/s/578923/ (CC0, Portuguese:
#              exercises the en/hi language restriction)
if [ "${SPEECH:-0}" = "1" ]; then
  for spec in "en:https://cdn.freesound.org/previews/381/381993_2276808-hq.mp3" \
              "pt:https://cdn.freesound.org/previews/578/578923_12898264-hq.mp3"; do
    lang="${spec%%:*}"; url="${spec#*:}"
    curl -sfL -m 60 -o "$OUT/speech_$lang.mp3" "$url"
    ffmpeg -hide_banner -loglevel error -nostdin -y \
      -f lavfi -i "color=c=0x223344:s=720x1280:r=30" -i "$OUT/speech_$lang.mp3" \
      -shortest -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -ar 48000 \
      "$OUT/speech_$lang.mp4"
    rm -f "$OUT/speech_$lang.mp3"
  done
  echo "[fixtures] speech clips: speech_en.mp4 speech_pt.mp4"
fi
