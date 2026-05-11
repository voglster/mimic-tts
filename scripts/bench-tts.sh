#!/usr/bin/env bash
# Bench mimic-tts synthesis latency. Hits the API directly (no CLI overhead),
# discards the audio. Runs N iterations of one phrase, reports per-call timings.
#
# Usage:
#   scripts/bench-tts.sh                          # piper, default phrase, 3 runs
#   scripts/bench-tts.sh -n 5 -v jim              # jim, 5 runs
#   scripts/bench-tts.sh -t "longer text here..."
set -euo pipefail

RUNS=3
VOICE="piper"
TEXT="Welcome to your new voice. I'm running on Chatterbox now, and honestly, I think it suits me better."

while getopts "n:v:t:" opt; do
  case "$opt" in
    n) RUNS="$OPTARG" ;;
    v) VOICE="$OPTARG" ;;
    t) TEXT="$OPTARG" ;;
    *) echo "usage: $0 [-n runs] [-v voice] [-t text]" >&2; exit 2 ;;
  esac
done

URL="${MIMIC_SERVER_URL:-$(grep -E '^server_url' ~/.config/mimic/config.toml 2>/dev/null | sed -E 's/.*= *"([^"]+)".*/\1/')}"
TOKEN="${MIMIC_API_TOKEN:-$(grep -E '^token' ~/.config/mimic/config.toml 2>/dev/null | sed -E 's/.*= *"([^"]+)".*/\1/')}"

if [[ -z "${URL:-}" ]]; then
  echo "MIMIC_SERVER_URL not set and not found in ~/.config/mimic/config.toml" >&2
  exit 1
fi

CHARS=${#TEXT}
echo "server : $URL"
echo "voice  : $VOICE"
echo "text   : $CHARS chars"
echo "runs   : $RUNS"
echo

# columns: total time, time-to-first-byte (often equals total since we don't stream),
# downloaded bytes, response size on disk via curl's -o /dev/null
printf "%-6s %10s %10s %10s\n" "run" "total_s" "ttfb_s" "audio_kB"

total_sum=0
ttfb_sum=0
for i in $(seq 1 "$RUNS"); do
  out=$(curl -s -o /tmp/mimic-bench.wav -w "%{time_total} %{time_starttransfer} %{size_download}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -F "text=${TEXT}" \
    -F "name=${VOICE}" \
    -F "language=English" \
    "${URL%/}/clone/tts")
  read -r total ttfb size <<<"$out"
  kb=$(awk "BEGIN {printf \"%.1f\", $size/1024}")
  printf "%-6s %10.3f %10.3f %10s\n" "$i" "$total" "$ttfb" "$kb"
  total_sum=$(awk "BEGIN {print $total_sum + $total}")
  ttfb_sum=$(awk "BEGIN {print $ttfb_sum + $ttfb}")
done

avg_total=$(awk "BEGIN {printf \"%.3f\", $total_sum / $RUNS}")
avg_ttfb=$(awk "BEGIN {printf \"%.3f\", $ttfb_sum / $RUNS}")
printf "\navg: total=%ss  ttfb=%ss  (%s chars → %s s/char)\n" \
  "$avg_total" "$avg_ttfb" "$CHARS" "$(awk "BEGIN {printf \"%.3f\", $avg_total / $CHARS}")"
