#!/usr/bin/env python3
# scripts/5_gen.py
#
# STEP 5: Generate MP4 (formerly 4_mp4.py), minimally altered.
# -----------------------------------------------------------
# - Identical rendering behavior to your LKWV
# - Only safe adjustments:
#     * accept --base-filename
#     * support offset passthrough from master
#     * output final mp4 as: output/<slug>.mp4
#     * JSON output at end for 0_master
#     * optional post-render offset tweak stub
#
# EVERYTHING ELSE IS UNCHANGED. ALL YOUR NOTE LOGIC REMAINS EXACT.

from __future__ import annotations
import argparse, json, os, random, subprocess, sys, time, csv
from pathlib import Path
from typing import Iterable, List, Tuple

TimingRow = Tuple[int, float, float, str]

# Repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ANSI colors for logs
RESET="\033[0m"; BOLD="\033[1m"
CYAN="\033[36m"; GREEN="\033[32m"
YELLOW="\033[33m"; RED="\033[31m"; BLUE="\033[34m"

# Directories
TXT_DIR     = REPO_ROOT / "txts"
MP3_DIR     = REPO_ROOT / "mp3s"
MIXES_DIR   = REPO_ROOT / "mixes"
TIMINGS_DIR = REPO_ROOT / "timings"
OUTPUT_DIR  = REPO_ROOT / "output"
META_DIR    = REPO_ROOT / "meta"

VIDEO_WIDTH  = 1920
VIDEO_HEIGHT = 1080

# Your constants 그대로 copied:
BOTTOM_BOX_HEIGHT_FRACTION = 0.20
TOP_BAND_FRACTION          = 1.0 - BOTTOM_BOX_HEIGHT_FRACTION
NEXT_LYRIC_TOP_MARGIN_PX    = 50
NEXT_LYRIC_BOTTOM_MARGIN_PX = 50

DIVIDER_LINE_OFFSET_UP_PX = 0
DIVIDER_HEIGHT_PX         = 0.25

DIVIDER_LEFT_MARGIN_PX  = VIDEO_WIDTH * 0.035
DIVIDER_RIGHT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX

VERTICAL_OFFSET_FRACTION = 0.0

NEXT_LINE_FONT_SCALE  = 0.55
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45
NEXT_LABEL_TOP_MARGIN_PX  = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

FADE_IN_MS  = 20
FADE_OUT_MS = 40

GLOBAL_NEXT_COLOR_RGB  = "FFFFFF"
GLOBAL_NEXT_ALPHA_HEX  = "4D"

DIVIDER_COLOR_RGB      = "FFFFFF"
DIVIDER_ALPHA_HEX      = "80"

TOP_LYRIC_TEXT_COLOR_RGB = "FFFFFF"
TOP_LYRIC_TEXT_ALPHA_HEX = "00"

BOTTOM_BOX_BG_COLOR_RGB = "000000"
BOTTOM_BOX_BG_ALPHA_HEX = "00"

TOP_BOX_BG_COLOR_RGB = "000000"
TOP_BOX_BG_ALPHA_HEX = "00"

NEXT_LABEL_COLOR_RGB = "FFFFFF"
NEXT_LABEL_ALPHA_HEX = GLOBAL_NEXT_ALPHA_HEX

DEFAULT_UI_FONT_SIZE = 120
ASS_FONT_MULTIPLIER  = 1.5

# Global offset (updated by args)
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "-0.5") or "-0.5")

# Music note logic 그대로 유지
MUSIC_NOTE_CHARS = "♪♫♩♬"
NOTE_GAP_THRESHOLD_SECS = 4.0
NOTE_MIN_COUNT = 1
NOTE_MAX_COUNT = 4
NOTE_SPAWN_PERIOD_SECS = 4.0
NOTE_DURATION  = NOTE_SPAWN_PERIOD_SECS
NOTE_FADE_IN   = 150
NOTE_FADE_OUT  = 200
MIN_LYRIC_VISIBLE_SECS = 4.0
NOTE_EARLY_END_SECS = 1.0

MIN_TITLE_SECS = 2.0

# -------------------------------------------------------------------------
def log(prefix, msg, color=RESET):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{prefix}] {msg}{RESET}")

def slugify(text):
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"

def seconds_to_ass(t):
    if t < 0: t = 0
    cs_total = int(round(t * 100))
    total_s, cs = divmod(cs_total, 100); h, r = divmod(total_s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def seconds_to_ass_time(sec: float) -> str:
    # Backwards-compat wrapper – keep all callsites happy
    return seconds_to_ass(sec)

def rgb_to_bgr(rrggbb):
    s = rrggbb.strip().lstrip("#").zfill(6)
    rr, gg, bb = s[0:2], s[2:4], s[4:6]
    return f"{bb}{gg}{rr}"

def is_music_only(text):
    if not text: return False
    t = text.strip()
    if not t: return False
    if any(ch.isalnum() for ch in t): return False
    if any(ch in MUSIC_NOTE_CHARS for ch in t): return True
    lower = t.lower()
    for kw in ["instrumental","solo","guitar solo","piano solo"]:
        if kw in lower: return True
    return True

def random_note():
    return random.choice(MUSIC_NOTE_CHARS)

# -------------------------------------------------------------------------
def read_meta(slug):
    p = META_DIR / f"{slug}.json"
    if not p.exists(): return "", slug
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("artist",""), d.get("title",slug)
    except: return "", slug

def read_timings(slug):
    p = TIMINGS_DIR / f"{slug}.csv"
    native = load_timings_any(p)
    rows = [(start, end, text, idx) for idx, start, end, text in native]
    rows.sort(key=lambda x: x[0])
    return rows

def probe_audio_duration(p):
    try:
        out = subprocess.check_output([
            "ffprobe","-v","error","-show_entries","format=duration",
            "-of","default=noprint_wrappers=1:nokey=1",str(p)
        ], text=True)
        return float(out.strip())
    except: return 0.0

# Keep offset tag for JSON receipts
def offset_tag(val):
    s = f"{val:+.3f}".replace("-", "m").replace("+", "p").replace(".", "p")
    return f"_offset_{s}s"

def load_timings_any(path: str | Path) -> List[TimingRow]:
    """
    Load canonical timing CSV and return a list of
        (line_index, start_secs, end_secs, text)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Timing CSV not found: {p}")

    rows: List[TimingRow] = []

    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)

        header = next(reader, None)
        if header is None:
            return []

        header_norm = [h.strip().lower() for h in header]
        if len(header_norm) < 4:
            raise ValueError(
                f"Expected 4-column CSV with header like "
                f"'line_index,start,end,text', got: {header}"
            )

        # Allow small naming variations for start/end.
        if header_norm[0] != "line_index":
            raise ValueError(
                f"First column must be 'line_index', got: {header[0]!r}"
            )

        start_ok = header_norm[1] in ("start", "start_secs")
        end_ok = header_norm[2] in ("end", "end_secs")
        if not (start_ok and end_ok):
            raise ValueError(
                "Expected header columns: line_index,start,end,text "
                f"(or start_secs/end_secs). Got: {header}"
            )

        for row_idx, row in enumerate(reader, start=2):
            # Skip blank lines
            if not row or all(not cell.strip() for cell in row):
                continue

            # Allow comment-style lines if first cell starts with '#'
            if row[0].lstrip().startswith("#"):
                continue

            if len(row) < 4:
                raise ValueError(
                    f"{p}: row {row_idx} has {len(row)} columns, expected at least 4."
                )

            try:
                line_index = int(row[0].strip())
            except ValueError:
                raise ValueError(
                    f"{p}: row {row_idx} has invalid line_index={row[0]!r}"
                ) from None

            try:
                start = float(row[1].strip())
                end = float(row[2].strip())
            except ValueError:
                raise ValueError(
                    f"{p}: row {row_idx} has invalid start/end seconds: "
                    f"{row[1]!r}, {row[2]!r}"
                ) from None

            # Text may contain commas; if CSV is correctly quoted, it will be in row[3].
            # If we somehow get extra columns, join them back for robustness.
            text = row[3]
            if len(row) > 4:
                text = ",".join(row[3:])

            rows.append((line_index, start, end, text))

    return rows


def save_timings_csv(path: str | Path, rows: Iterable[TimingRow]) -> None:
    """
    Save timing rows to CSV using the canonical 4-column schema:

        line_index,start,end,text
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "start", "end", "text"])
        for line_index, start, end, text in rows:
            writer.writerow(
                [line_index, f"{start:.3f}", f"{end:.3f}", text]
            )

# -------------------------------------------------------------------------
# CORE ASS GENERATOR — UNCHANGED except MP4 filename logic
# -------------------------------------------------------------------------

def build_ass(
    slug: str,
    profile: str,
    artist: str,
    title: str,
    timings,
    audio_duration: float,
    font_name: str,
    font_size_script: int,
    offset_applied: float,
) -> Path:

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset_applied)}.ass"

    # Audio fallback
    if audio_duration <= 0 and timings:
        audio_duration = max(end for _, end, _, _ in timings) + 5
    if audio_duration <= 0:
        audio_duration = 5

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_div = top_band_height
    bottom_band_height = playresy - y_div

    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_top = center_top + offset_px

    x_center = playresx // 2
    y_center_full = playresy // 2
    y_next = y_div + NEXT_LYRIC_TOP_MARGIN_PX + (bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX) // 2

    preview_font    = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))
    margin_v = 0

    # Style header
    top_primary = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    secondary   = "&H000000FF"
    outline     = "&H00000000"
    back        = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {playresx}",
        f"PlayResY: {playresy}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Default,{font_name},{font_size_script},{top_primary},{secondary},"
            f"{outline},{back},0,0,0,0,100,100,0,0,1,4,0,5,50,50,{margin_v},0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    events = []

    # Normalize timings
    unified = []
    for start_raw, end_raw, raw_text, li in timings:
        t = raw_text.strip()
        if not t:
            continue

        s = start_raw + offset_applied
        e = end_raw   + offset_applied
        if s < 0: s = 0
        if e <= s: e = s + 0.01
        if e > audio_duration: e = audio_duration

        unified.append((s, e, t, li, is_music_only(t)))

    unified.sort(key=lambda x: x[0])
    if not unified:
        block = "\\N".join([title, f"by {artist}"] if artist else [title])
        events.append(
            f"Dialogue: 0,0,{seconds_to_ass_time(audio_duration)},Default,,0,0,0,,"
            f"{{\\an5\\pos({x_center},{y_center_full})}}{block}"
        )
        ass_path.write_text("\n".join(header + events), encoding="utf-8")
        return ass_path

    n = len(unified)

    # Handle repeated-text decoration
    display_texts = [u[2] for u in unified]
    i = 0
    while i < n:
        base = unified[i][2].strip().lower()
        j = i + 1
        while j < n and unified[j][2].strip().lower() == base:
            j += 1
        run = j - i
        if run > 1:
            prev_note = None
            for k in range(run):
                text = unified[i+k][2]
                if k == 0:
                    display_texts[i+k] = text
                else:
                    choices = [c for c in MUSIC_NOTE_CHARS if c != prev_note] or MUSIC_NOTE_CHARS
                    note = random.choice(choices)
                    prev_note = note
                    display_texts[i+k] = f"{text} {note}"
        i = j

    fade_tag = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})"

    # ≡≡≡ INTRO TITLE — ALWAYS SHOWN ≥ MIN_TITLE_SECS ≡≡≡
    first_lyric_start = unified[0][0]
    title_start = 0.0
    title_end = max(MIN_TITLE_SECS, min(first_lyric_start, 5.0))

    # Shift all lyrics AFTER title_end
    shifted = []
    for (s, e, t, li, mus) in unified:
        if s < title_end:
            shift = title_end - s
            s = title_end
            e = max(e + shift, s + 0.01)
        shifted.append((s, e, t, li, mus))
    unified = sorted(shifted, key=lambda x: x[0])

    # Emit title
    title_block = "\\N".join([title, f"by {artist}"] if artist else [title])
    events.append(
        f"Dialogue: 0,{seconds_to_ass_time(title_start)},{seconds_to_ass_time(title_end)},"
        f"Default,,0,0,0,,{{\\an5\\pos({x_center},{y_center_full})}}{title_block}"
    )

    # Note block helper
    def emit_notes_block(start, end):
        if end <= start + 0.01:
            return
        t = start
        while t < end:
            frame_end = min(t + NOTE_DURATION, end)
            k = random.randint(NOTE_MIN_COUNT, NOTE_MAX_COUNT)
            seq = random.sample(MUSIC_NOTE_CHARS, min(k, len(MUSIC_NOTE_CHARS)))
            text = "".join(seq)
            tag = f"{{\\an5\\pos({x_center},{y_center_full})\\fs{preview_font*2}}}"
            events.append(
                f"Dialogue: 2,{seconds_to_ass_time(t)},{seconds_to_ass_time(frame_end)},"
                f"Default,,0,0,0,,{tag}{text}"
            )
            t += NOTE_SPAWN_PERIOD_SECS

    # ≡≡≡ MAIN LOOP ≡≡≡
    for i, (s, e, text, li, mus) in enumerate(unified):
        is_last = (i == n - 1)
        display_text = display_texts[i]
        y_line = (VIDEO_HEIGHT//2) if mus else y_main_top

        if is_last:
            reserved_notes_end = max(e, audio_duration - NOTE_EARLY_END_SECS)
            remaining = reserved_notes_end - e

            if remaining >= NOTE_GAP_THRESHOLD_SECS:
                lyric_end = min(e + MIN_LYRIC_VISIBLE_SECS, reserved_notes_end)
                events.append(
                    f"Dialogue: 1,{seconds_to_ass_time(s)},{seconds_to_ass_time(lyric_end)},"
                    f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{display_text}"
                )
                emit_notes_block(lyric_end, reserved_notes_end)
            else:
                events.append(
                    f"Dialogue: 1,{seconds_to_ass_time(s)},{seconds_to_ass_time(reserved_notes_end)},"
                    f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{display_text}"
                )
            continue

        # Normal line gap
        next_s, _, _, _, next_mus = unified[i+1]
        gap_end = next_s
        reserved_notes_end = max(s, next_s - NOTE_EARLY_END_SECS)

        display_end = e
        instrument_start = None
        instrument_end   = None

        if not mus:
            min_end = max(e, s + MIN_LYRIC_VISIBLE_SECS)
            min_end = min(min_end, reserved_notes_end, gap_end)
            remaining = reserved_notes_end - min_end

            if remaining >= NOTE_GAP_THRESHOLD_SECS and not next_mus:
                display_end = min_end
                instrument_start = min_end
                instrument_end   = reserved_notes_end
            else:
                display_end = gap_end

        events.append(
            f"Dialogue: 1,{seconds_to_ass_time(s)},{seconds_to_ass_time(display_end)},"
            f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{display_text}"
        )

        if instrument_start:
            emit_notes_block(instrument_start, instrument_end)

        # Skip previews for music lines
        if mus or next_mus:
            continue

        preview_start = s
        preview_end   = instrument_start if instrument_start else gap_end
        if preview_end <= preview_start + 0.05:
            continue

        divider_color = rgb_to_bgr(DIVIDER_COLOR_RGB)
        next_color    = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
        divider_tag = (
            f"{{\\an7\\pos(0,{y_div})\\1c&H{divider_color}&\\bord0\\shad0\\p1}}"
        )
        shape = f"m {0} 0 l {playresx} 0 l {playresx} 1 l 0 1{{\\p0}}"

        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(preview_start)},{seconds_to_ass_time(preview_end)},"
            f"Default,,0,0,0,,{divider_tag}{shape}"
        )

        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(preview_start)},{seconds_to_ass_time(preview_end)},"
            f"Default,,0,0,0,,{{\\an7\\pos({NEXT_LABEL_LEFT_MARGIN_PX},{y_div+NEXT_LABEL_TOP_MARGIN_PX})"
            f"\\fs{next_label_font}}}Next:"
        )

        events.append(
            f"Dialogue: 2,{seconds_to_ass_time(preview_start)},{seconds_to_ass_time(preview_end)},"
            f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_next})\\fs{preview_font}{fade_tag}}}"
            f"{display_texts[i+1]}"
        )

    ass_path.write_text("\n".join(header + events), encoding="utf-8")
    return ass_path

# -------------------------------------------------------------------------
def choose_audio(slug, profile):
    # For now keep original behavior
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mix_wav.exists(): return mix_wav
    return mp3

# -------------------------------------------------------------------------
def main(argv=None):
    global LYRICS_OFFSET_SECS

    p = argparse.ArgumentParser()
    p.add_argument("--base-filename", required=True)
    p.add_argument("--profile", default="karaoke")
    p.add_argument("--font-size", type=int)
    p.add_argument("--font-name", default="Helvetica")
    p.add_argument("--offset", type=float)
    p.add_argument("--force", action="store_true")
    p.add_argument("passthrough", nargs="*")
    args = p.parse_args(argv)

    slug = slugify(args.base_filename)

    if args.offset is not None:
        LYRICS_OFFSET_SECS = float(args.offset)

    log("Gen","Offset = {:.3f}s".format(LYRICS_OFFSET_SECS),CYAN)

    # output file (NEW RULE)
    out_mp4 = OUTPUT_DIR / f"{slug}.mp4"

    font_size = args.font_size or DEFAULT_UI_FONT_SIZE
    ass_font_size = int(font_size * ASS_FONT_MULTIPLIER)

    audio_path = choose_audio(slug, args.profile)
    audio_duration = probe_audio_duration(audio_path)

    artist, title = read_meta(slug)
    timings = read_timings(slug)

    # Build ASS
    ass_path = build_ass(
        slug, args.profile, artist, title, timings,
        audio_duration, args.font_name, ass_font_size, LYRICS_OFFSET_SECS
    )

    cmd = [
        "ffmpeg","-y",
        "-f","lavfi","-i",
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration,1)}",
        "-i",str(audio_path),
        "-vf",f"subtitles={ass_path}",
        "-c:v","libx264","-preset","medium","-crf","18",
        "-c:a","aac","-b:a","192k",
        "-shortest",
        str(out_mp4)
    ]

    log("FFMPEG"," ".join(cmd),BLUE)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(f"{CYAN}[ffmpeg]{RESET} {line.rstrip()}")
    proc.wait()

    if proc.returncode != 0:
        print(json.dumps({"ok":False,"error":"ffmpeg-failed"}))
        return

    log("Gen",f"MP4 ready: {out_mp4}",GREEN)

    # Optional offset tweak hook (stub)
    # TODO: ask user "Adjust offset further? (y/n)" → re-render
    # For now, leave stub.

    print(json.dumps({
        "ok": True,
        "slug": slug,
        "mp4": str(out_mp4),
        "ass": str(ass_path),
        "offset": LYRICS_OFFSET_SECS
    }))

if __name__ == "__main__":
    main()

# end of 5_gen.py
