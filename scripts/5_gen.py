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
import argparse, json, os, random, subprocess, sys, time
from pathlib import Path

# Repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load timing parser
from scripts.timings_io import load_timings_any  # unchanged

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
TITLE_EXTRA_OFFSET_FRACTION = -0.20  

NEXT_LINE_FONT_SCALE  = 0.55
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45
NEXT_LABEL_TOP_MARGIN_PX  = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

FADE_IN_MS  = 75
FADE_OUT_MS = 75

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

    # Audio duration fallback
    if audio_duration <= 0.0 and timings:
        last_end = max(end for (start, end, _t, _li) in timings)
        audio_duration = last_end + 5.0
    if audio_duration <= 0.0:
        audio_duration = 5.0

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT

    # Geometry
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_div = top_band_height
    bottom_band_height = playresy - y_div

    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_top = center_top + offset_px

    x_center = playresx // 2
    y_center_full = playresy // 2

    line_y = max(0, y_div - DIVIDER_LINE_OFFSET_UP_PX)

    inner_bottom_height = max(
        1,
        bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX
    )
    y_next = y_div + NEXT_LYRIC_TOP_MARGIN_PX + inner_bottom_height // 2

    preview_font    = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))
    margin_v = 0

    # ASS colors
    top_primary = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    secondary   = "&H000000FF"
    outline     = "&H00000000"
    back        = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {playresx}",
        f"PlayResY: {playresy}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
        (
            f"Style: Default,{font_name},{font_size_script},"
            f"{top_primary},{secondary},{outline},{back},"
            "0,0,0,0,100,100,0,0,1,4,0,5,50,50,"
            f"{margin_v},0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    def esc(s: str) -> str:
        return s.replace("{","(").replace("}"," )").replace("\n", r"\N")

    events = []

    # Normalize timings
    unified = []
    for start_raw, end_raw, raw_text, li in timings:
        t = (raw_text or "").strip()
        if not t:
            continue

        start = start_raw + offset_applied
        end   = end_raw   + offset_applied

        if start < 0:
            start = 0
        if end <= start:
            end = start + 0.01
        if audio_duration > 0 and end > audio_duration:
            end = audio_duration

        music_only = is_music_only(t)
        unified.append((start, end, t, li, music_only))

    unified.sort(key=lambda x: x[0])

    if not unified:
        block = "\\N".join([title, f"by {artist}"] if artist else [title])
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(0)},{seconds_to_ass_time(audio_duration)},"
            f"Default,,0,0,0,,{{\\an5\\pos({x_center},{y_center_full})}}{esc(block)}"
        )
        ass_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
        return ass_path

    n = len(unified)

    # Build repeated-line display_texts
    display_texts = [t for (_s, _e, t, _li, _mus) in unified]

    i = 0
    while i < n:
        base_text = unified[i][2]
        norm = base_text.strip().lower()
        j = i + 1
        while j < n and unified[j][2].strip().lower() == norm:
            j += 1
        run_len = j - i
        if run_len > 1:
            prev_note = None
            for k in range(run_len):
                if k == 0:
                    display_texts[i+k] = unified[i+k][2]
                else:
                    choices = [c for c in MUSIC_NOTE_CHARS if c != prev_note] or [MUSIC_NOTE_CHARS[0]]
                    note = random.choice(choices)
                    prev_note = note
                    display_texts[i+k] = f"{unified[i+k][2]} {note}"
        i = j

    fade_tag = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})"

    next_color       = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
    divider_color    = rgb_to_bgr(DIVIDER_COLOR_RGB)
    next_label_color = rgb_to_bgr(NEXT_LABEL_COLOR_RGB)

    divider_height = max(0.5, DIVIDER_HEIGHT_PX)
    x_left  = float(DIVIDER_LEFT_MARGIN_PX)
    x_right = float(playresx - DIVIDER_RIGHT_MARGIN_PX)

    label_x = NEXT_LABEL_LEFT_MARGIN_PX
    label_y = y_div + NEXT_LABEL_TOP_MARGIN_PX

    # Notes emitter
    def emit_notes_block(t_start, t_end):
        if t_end <= t_start + 0.05:
            return
        t = t_start
        while t < t_end - 0.05:
            frame_end = min(t + NOTE_DURATION, t_end)
            k = random.randint(NOTE_MIN_COUNT, NOTE_MAX_COUNT)
            if k >= len(MUSIC_NOTE_CHARS):
                seq = list(MUSIC_NOTE_CHARS)
                random.shuffle(seq)
                cluster = "".join(seq)
            else:
                cluster = "".join(random.sample(MUSIC_NOTE_CHARS, k))

            tag = (
                f"{{\\an5\\pos({x_center},{y_center_full})"
                f"\\fs{preview_font*2}\\fad({NOTE_FADE_IN},{NOTE_FADE_OUT})}}"
            )
            events.append(
                f"Dialogue: 2,{seconds_to_ass_time(t)},{seconds_to_ass_time(frame_end)},"
                f"Default,,0,0,0,,{tag}{cluster}"
            )
            t += NOTE_SPAWN_PERIOD_SECS

    # INTRO TITLE
    first_lyric_start = unified[0][0]
    if first_lyric_start > 0.05:
        title_start = 0.0
        base_title_end = min(first_lyric_start, 5.0)
        reserved_notes_end = max(0.0, first_lyric_start - NOTE_EARLY_END_SECS)

        if reserved_notes_end <= title_start:
            title_end = first_lyric_start
            intro_start = intro_end = None
        else:
            min_title_display = max(base_title_end, title_start + MIN_LYRIC_VISIBLE_SECS)
            min_title_display = min(min_title_display, reserved_notes_end, first_lyric_start)
            remaining = reserved_notes_end - min_title_display

            if remaining >= NOTE_GAP_THRESHOLD_SECS:
                title_end = min_title_display
                intro_start = min_title_display
                intro_end   = reserved_notes_end
            else:
                title_end = first_lyric_start
                intro_start = intro_end = None

        block = "\\N".join([title, f"by {artist}"] if artist else [title])
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(title_start)},{seconds_to_ass_time(title_end)},"
            f"Default,,0,0,0,,{{\\an5\\pos({x_center},{y_center_full})}}{esc(block)}"
        )

        if intro_start is not None:
            emit_notes_block(intro_start, intro_end)

    # MAIN LINES ------------------------------------------
    for i, (start_i, end_i, text_i, li_i, mus_i) in enumerate(unified):

        # ===========================
        # LAST LYRIC LINE
        # ===========================
        if i == n - 1:
            display_text_i = display_texts[i]
            y_line = (VIDEO_HEIGHT // 2) if mus_i else y_main_top

            song_end = audio_duration
            reserved = max(end_i, song_end - NOTE_EARLY_END_SECS)
            remaining = reserved - end_i

            if remaining >= NOTE_GAP_THRESHOLD_SECS:
                lyric_end = min(end_i + MIN_LYRIC_VISIBLE_SECS, reserved)

                events.append(
                    f"Dialogue: 1,{seconds_to_ass_time(start_i)},{seconds_to_ass_time(lyric_end)},"
                    f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{esc(display_text_i)}"
                )

                emit_notes_block(lyric_end, reserved)

            else:
                lyric_end = reserved
                events.append(
                    f"Dialogue: 1,{seconds_to_ass_time(start_i)},{seconds_to_ass_time(lyric_end)},"
                    f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{esc(display_text_i)}"
                )

            continue

        # ===========================
        # NORMAL LINES
        # ===========================
        next_start, next_end, next_text, _li_n, next_mus = unified[i+1]
        gap_end = next_start
        reserved_notes_end = max(start_i, next_start - NOTE_EARLY_END_SECS)

        if gap_end < start_i:
            gap_end = start_i
        if reserved_notes_end < start_i:
            reserved_notes_end = start_i

        display_end = end_i
        instrument_start = None
        instrument_end = None

        if not mus_i:
            min_end = max(end_i, start_i + MIN_LYRIC_VISIBLE_SECS)
            min_end = min(min_end, reserved_notes_end, gap_end)
            remaining = reserved_notes_end - min_end

            if remaining >= NOTE_GAP_THRESHOLD_SECS and not next_mus:
                display_end = min_end
                instrument_start = min_end
                instrument_end = reserved_notes_end
            else:
                display_end = gap_end

        display_text_i = display_texts[i]
        y_line = (VIDEO_HEIGHT // 2) if mus_i else y_main_top

        events.append(
            f"Dialogue: 1,{seconds_to_ass_time(start_i)},{seconds_to_ass_time(display_end)},"
            f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{esc(display_text_i)}"
        )

        if instrument_start is not None:
            emit_notes_block(instrument_start, instrument_end)

        if mus_i or next_mus:
            continue

        preview_start = start_i
        preview_end = instrument_start if instrument_start is not None else gap_end

        if preview_end <= preview_start + 0.05:
            continue

        # Divider bar
        div_tag = (
            f"{{\\an7\\pos(0,{line_y})"
            f"\\1c&H{divider_color}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\bord0\\shad0\\p1}}"
        )
        shape = (
            f"m {x_left} 0 l {x_right} 0 "
            f"l {x_right} {divider_height} l {x_left} {divider_height}{{\\p0}}"
        )

        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(preview_start)},{seconds_to_ass_time(preview_end)},"
            f"Default,,0,0,0,,{div_tag}{shape}"
        )

        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(preview_start)},{seconds_to_ass_time(preview_end)},"
            f"Default,,0,0,0,,{{\\an7\\pos({label_x},{label_y})\\fs{next_label_font}"
            f"\\1c&H{next_label_color}&\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&}}Next:"
        )

        events.append(
            f"Dialogue: 2,{seconds_to_ass_time(preview_start)},{seconds_to_ass_time(preview_end)},"
            f"Default,,0,0,0,,{{\\an5\\pos({playresx//2},{y_next})\\fs{preview_font}"
            f"\\1c&H{next_color}&\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&{fade_tag}}}"
            f"{esc(display_texts[i+1])}"
        )

    ass_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
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
