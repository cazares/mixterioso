#!/usr/bin/env bash
set -euo pipefail

# new_song_with_automated_timings.sh
# Downloads audio (yt-dlp URL or search), aligns lyrics.txt -> CSV (line,start),
# then calls car_karaoke_time.py with your usual flags. macOS-friendly.

# -------------------------
# Defaults / knobs
# -------------------------
MODEL="large-v3"                 # stable-ts model; try 'medium' if you want faster
OPEN_AFTER=false                 # open Finder on completion
PLAY_PREVIEW=false               # quick audio preview with afplay
PASSTHRU_ARGS=()                 # holds args after "--" to forward to car_karaoke_time.py
LYRICS=""
URL=""
QUERY=""
AUDIO_FILE=""                    # if you already have a local mp3
TITLE_OVERRIDE=""                # optional explicit base title (for output file naming)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SONGS_DIR="$REPO_ROOT/songs"
LYRICS_DIR="$REPO_ROOT/lyrics"
SCRIPTS_DIR="$REPO_ROOT/scripts"
ALIGN_ENV="$REPO_ROOT/lyrics-align-env"
ALIGNER_PY="$SCRIPTS_DIR/lyrics_to_csv.py"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --lyrics "lyrics/your_song.txt" [--url URL | --query "artist song" | --file songs/your.mp3]
                   [--model large-v3] [--title "BaseTitle"] [--open] [--play] [--] [car_karaoke_time flags...]

Examples:
  # Use a YouTube URL + lyrics file:
  $(basename "$0") --lyrics "lyrics/scar_tissue.txt" --url "https://www.youtube.com/watch?v=XXXXX" -- --high-quality --font-size 140

  # Use a search query (picks most viewed):
  $(basename "$0") --lyrics "lyrics/scar_tissue.txt" --query "Red Hot Chili Peppers Scar Tissue" -- --offset-video -1.0

  # Use an existing local MP3:
  $(basename "$0") --lyrics "lyrics/scar_tissue.txt" --file "songs/scar_tissue.mp3"

Flags:
  --lyrics PATH         Required. Plain-text lyrics (one on-screen line per line).
  --url URL            YouTube URL to download audio.
  --query "text"       Search text to find most viewed result via yt-dlp.
  --file PATH          Use an existing local MP3 instead of downloading.
  --title "BaseTitle"  Optional: override output base name (CSV & final video context).
  --model NAME         stable-ts model (default: large-v3).
  --open               Open the output folders in Finder when done.
  --play               Play a short preview of the audio after download.
  --                    Everything after -- is passed to car_karaoke_time.py.
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Try: brew install $1"; }

sanitize() {
  # make a safe filename base
  local s="$1"
  s="${s//[^A-Za-z0-9._ -]/}"     # strip odd chars
  s="$(echo "$s" | tr '[:upper:]' '[:lower:]' | sed -Ee 's/[[:space:]]+/_/g' -e 's/_+/_/g' -e 's/^_//; s/_$//')"
  echo "$s"
}

# -------------------------
# Parse args
# -------------------------
if [[ $# -eq 0 ]]; then usage; exit 1; fi

while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --lyrics)        LYRICS="${2:-}"; shift 2 ;;
    --url)           URL="${2:-}"; shift 2 ;;
    --query)         QUERY="${2:-}"; shift 2 ;;
    --file)          AUDIO_FILE="${2:-}"; shift 2 ;;
    --model)         MODEL="${2:-}"; shift 2 ;;
    --title)         TITLE_OVERRIDE="${2:-}"; shift 2 ;;
    --open)          OPEN_AFTER=true; shift ;;
    --play)          PLAY_PREVIEW=true; shift ;;
    --help|-h)       usage; exit 0 ;;
    --)              shift; PASSTHRU_ARGS=("$@"); break ;;
    *)               echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

[[ -f "$LYRICS" ]] || die "--lyrics file not found: $LYRICS"

mkdir -p "$SONGS_DIR" "$LYRICS_DIR" "$SCRIPTS_DIR"

# -------------------------
# Ensure deps that we use in bash
# -------------------------
need_cmd python3
need_cmd ffmpeg
need_cmd yt-dlp

# Optional: jq makes selecting the most-viewed hit easier; if missing we fallback to Python.
HAS_JQ=true
command -v jq >/dev/null 2>&1 || HAS_JQ=false

# -------------------------
# Get or set AUDIO + TITLE
# -------------------------
TITLE_BASENAME=""
if [[ -n "$AUDIO_FILE" ]]; then
  [[ -f "$AUDIO_FILE" ]] || die "--file not found: $AUDIO_FILE"
  TITLE_BASENAME="$(basename "${TITLE_OVERRIDE:-$(basename "$AUDIO_FILE" .mp3)}" .m4a)"
  TITLE_BASENAME="$(sanitize "$TITLE_BASENAME")"
elif [[ -n "$URL" || -n "$QUERY" ]]; then
  # Resolve a URL (from query if necessary), then download best audio as MP3
  if [[ -n "$QUERY" && -z "$URL" ]]; then
    echo "Searching YouTube for most-viewed: $QUERY"
    if $HAS_JQ; then
      URL="$(yt-dlp --dump-json "ytsearch50:$QUERY" | jq -r 'map(select(.live_status == "not_live")) | max_by(.view_count) | .webpage_url')"
    else
      URL="$(yt-dlp --dump-json "ytsearch50:$QUERY" | python3 - "$QUERY" <<'PY'
import sys, json
best = None
for line in sys.stdin:
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if obj.get("live_status") and obj["live_status"] != "not_live":
        continue
    vc = obj.get("view_count") or -1
    if best is None or vc > best.get("view_count", -1):
        best = obj
print(best["webpage_url"] if best else "")
PY
)"
    fi
    [[ -n "$URL" && "$URL" =~ ^https?:// ]] || die "No suitable result for query."
  fi
  echo "Downloading best audio from: $URL"
  # Predict final filename for mp3 after extraction
  PREDICTED="$(yt-dlp --no-playlist -o "%(title)s.%(ext)s" -x --audio-format mp3 --audio-quality 0 --skip-download "$URL")"
  TITLE_BASENAME="$(sanitize "${TITLE_OVERRIDE:-$(basename "$PREDICTED" .mp3)}")"
  AUDIO_FILE="$SONGS_DIR/${TITLE_BASENAME}.mp3"
  yt-dlp --no-playlist -o "$SONGS_DIR/%(title)s.%(ext)s" -x --audio-format mp3 --audio-quality 0 --add-metadata "$URL"
  # If file was saved with a slightly different normalized title, try to locate it
  if [[ ! -f "$AUDIO_FILE" ]]; then
    # find the newest mp3 in songs dir
    CANDIDATE="$(ls -t "$SONGS_DIR"/*.mp3 2>/dev/null | head -n1 || true)"
    [[ -n "$CANDIDATE" ]] && mv -f "$CANDIDATE" "$AUDIO_FILE"
  fi
  [[ -f "$AUDIO_FILE" ]] || die "Download failed; MP3 not found."
else
  die "Provide one of: --file, --url, or --query."
fi

echo "Audio: $AUDIO_FILE"
echo "Lyrics: $LYRICS"
echo "Base title: $TITLE_BASENAME"

# Optional preview
if $PLAY_PREVIEW; then
  if command -v afplay >/dev/null 2>&1; then
    echo "Playing 5s preview..."
    afplay "$AUDIO_FILE" &
    sleep 5
    pkill -f "afplay" || true
  fi
fi

# -------------------------
# Ensure align environment + aligner script
# -------------------------
if [[ ! -x "$ALIGN_ENV/bin/python3" ]]; then
  echo "Creating alignment venv at $ALIGN_ENV"
  python3 -m venv "$ALIGN_ENV"
  # shellcheck disable=SC1091
  source "$ALIGN_ENV/bin/activate"
  pip3 install --upgrade pip
  pip3 install stable-ts openai-whisper thefuzz numpy tqdm
  # If you later enable ForceAlign on Python 3.13:
  # pip3 install audioop-lts
  deactivate
fi

if [[ ! -f "$ALIGNER_PY" ]]; then
  echo "Writing robust aligner to $ALIGNER_PY"
  cat > "$ALIGNER_PY" <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, csv, os, re, shutil, subprocess, sys, tempfile
from typing import List, Dict, Tuple

def die(msg: str): print("ERROR:", msg, file=sys.stderr); sys.exit(1)
def norm_tokens(s: str): return re.findall(r"[a-z0-9']+", s.lower())

def read_lyrics(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [ln.rstrip() for ln in f if ln.strip()]

def ffmpeg_preconvert(in_audio: str) -> tuple[str, str]:
    if not shutil.which("ffmpeg"): die("ffmpeg not found")
    tmpdir = tempfile.mkdtemp(prefix="alignwav_")
    out_wav = os.path.join(tmpdir, "audio_16k_mono.wav")
    cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error","-i",in_audio,"-ac","1","-ar","16000",out_wav]
    subprocess.run(cmd, check=True)
    return out_wav, tmpdir

def words_from_stable(result) -> list[dict]:
    words = []
    for seg in result.to_dict().get("segments", []):
        for w in seg.get("words", []):
            if w.get("word") and isinstance(w.get("start"), (int,float)) and isinstance(w.get("end"), (int,float)):
                words.append({"word": w["word"], "start": float(w["start"]), "end": float(w["end"])})
    return words

def assign_lines_robust(words: List[Dict], lines: List[str],
                        start_wi: int = 0, search_ahead: int = 400,
                        skip_max: int = 6, min_cover: float = 0.60
                        ) -> List[tuple[str, float, float]]:
    W = [ (norm_tokens(w["word"]) or [""])[0] for w in words ]
    out, wi = [], max(0, start_wi)
    for line in lines:
        toks = norm_tokens(line)
        if not toks:
            prev_end = out[-1][2] if out else 0.0
            out.append((line, round(prev_end,3), round(prev_end,3)))
            continue
        best = None
        end_window = min(len(W), wi + search_ahead)
        for k in range(wi, end_window):
            if W[k] != toks[0]: continue
            m, j, last = 1, k + 1, k
            while m < len(toks) and j < len(W):
                hopped = 0
                while j < len(W) and W[j] != toks[m] and hopped < skip_max:
                    j += 1; hopped += 1
                if j < len(W) and W[j] == toks[m]:
                    last = j; m += 1; j += 1
                else:
                    break
            score = m / max(1, len(toks))
            if (best is None) or (score > best[0]):
                best = (score, k, last)
                if score >= 0.98: break
        if best and best[0] >= min_cover:
            _, k, last = best
            out.append((line, round(words[k]["start"],3), round(words[last]["end"],3)))
            wi = min(last + 1, k + search_ahead)
        else:
            prev_end = out[-1][2] if out else 0.0
            out.append((line, round(prev_end,3), round(prev_end,3)))
    return out

def coverage_report(rows): 
    pinned = sum(1 for _, s, e in rows if abs(s - e) < 1e-6)
    return len(rows), pinned

def planA(wav, lines, model):
    import stable_whisper
    model = stable_whisper.load_model(model)
    res = model.align(wav, "\n".join(lines), language="en")
    return assign_lines_robust(words_from_stable(res), lines)

def planB(wav, lines, model):
    import stable_whisper
    model = stable_whisper.load_model(model)
    res = model.transcribe(wav, language="en")
    return assign_lines_robust(words_from_stable(res), lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--format", default="line_start", choices=["line_start","line_start_end"])
    ap.add_argument("--min-cover", type=float, default=0.60)
    ap.add_argument("--search-ahead", type=int, default=400)
    ap.add_argument("--skip-max", type=int, default=6)
    args = ap.parse_args()

    lines = read_lyrics(args.lyrics)
    wav, tmpdir = ffmpeg_preconvert(args.audio)
    rows = None
    try:
        print(">>> Plan A: align()")
        rows = planA(wav, lines, args.model)
        total, pinned = coverage_report(rows)
        print(f"Plan A -> {total} lines, pinned={pinned}")
        if pinned > max(0, total//10): rows = None
    except Exception as e:
        print("Plan A failed:", e)

    if rows is None:
        try:
            print(">>> Plan B: transcribe+map")
            rows = planB(wav, lines, args.model)
            total, pinned = coverage_report(rows)
            print(f"Plan B -> {total} lines, pinned={pinned}")
        except Exception as e:
            print("Plan B failed:", e)

    try: shutil.rmtree(tmpdir)
    except Exception: pass

    if rows is None: die("All alignment plans failed")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if args.format == "line_start":
            w.writerow(["line","start"])
            for line, start, _end in rows:
                w.writerow([line, f"{start:.3f}"])
        else:
            w.writerow(["line","start","end"])
            for line, start, end in rows:
                w.writerow([line, f"{start:.3f}", f"{end:.3f}"])
    total, pinned = coverage_report(rows)
    print(f"✅ wrote {args.out}  |  lines={total}  pinned={pinned} ({pinned/total:.1%})")

if __name__ == "__main__":
    main()
# end of lyrics_to_csv.py
PY
  chmod +x "$ALIGNER_PY"
fi

# -------------------------
# Align -> CSV (line,start)
# -------------------------
CSV_OUT="$LYRICS_DIR/${TITLE_BASENAME}_aligned.csv"
echo "Aligning lyrics -> $CSV_OUT (model=$MODEL)…"
# shellcheck disable=SC1091
source "$ALIGN_ENV/bin/activate"
python3 "$ALIGNER_PY" \
  --audio "$AUDIO_FILE" \
  --lyrics "$LYRICS" \
  --out "$CSV_OUT" \
  --model "$MODEL" \
  --format line_start
deactivate

# -------------------------
# Kick off your renderer
# -------------------------
echo "Running car_karaoke_time.py …"
python3 "$SCRIPTS_DIR/car_karaoke_time.py" \
  --csv "$CSV_OUT" \
  --mp3 "$AUDIO_FILE" \
  "${PASSTHRU_ARGS[@]}"

# -------------------------
# Optional niceties
# -------------------------
$OPEN_AFTER && { open "$SONGS_DIR" || true; open "$LYRICS_DIR" || true; }

echo "✅ Done. CSV: $CSV_OUT"
# end of new_song_with_automated_timings.sh
