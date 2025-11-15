#!/usr/bin/env python3
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

BASE_DIR = Path(__file__).resolve().parent.parent
TIMINGS_DIR = BASE_DIR / "timings"
META_DIR = BASE_DIR / "meta"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
OUTPUT_DIR = BASE_DIR / "output"

WIDTH = 1920
HEIGHT = 1080
FPS = 30

LYRIC_MIN_DURATION = 1.0  # seconds
LYRIC_FUDGE_BEFORE_NEXT = 0.25  # seconds
NOTE_GAP_THRESHOLD = 6.0  # seconds: if gap >= this, insert a note
NOTE_INSET = 1.0  # seconds: start note 1s after prev_end, end 1s before next_start

DEFAULT_FONT_NAME = "Arial"
DEFAULT_FONT_SIZE = 120


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


@dataclass
class Event:
    start: float
    end: float
    text: str
    kind: str = "lyric"  # "lyric" or "note"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Render karaoke MP4 with ASS overlay.")
    p.add_argument("--slug", required=True, help="Song slug, e.g. 'ascension'")
    p.add_argument("--profile", default="karaoke", help="Mix profile, e.g. 'karaoke'")
    p.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Global lyrics offset in seconds (neg=sooner, pos=later)",
    )
    p.add_argument(
        "--font-size",
        type=int,
        default=DEFAULT_FONT_SIZE,
        help="Base font size for lyrics",
    )
    p.add_argument(
        "--font-name",
        default=DEFAULT_FONT_NAME,
        help="Font name for lyrics (must be installed on system)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force re-render even if MP4 already exists",
    )
    p.add_argument(
        "--no-post-ui",
        action="store_true",
        help="Skip interactive post-render UI (for 0_master)",
    )
    return p.parse_args(argv)


def format_offset_tag(offset: float) -> str:
    sign = "p" if offset >= 0 else "m"
    value = abs(offset)
    return f"{sign}{value:0.3f}s".replace(".", "p")


def resolve_offset(cli_offset: float) -> float:
    env_val = os.getenv("KARAOKE_OFFSET_SECS")
    if env_val:
        try:
            env_off = float(env_val)
            # If CLI explicitly passes non-zero, honor CLI; else env
            if abs(cli_offset) < 1e-6:
                cli_offset = env_off
        except ValueError:
            log("OFFSET", f"Invalid KARAOKE_OFFSET_SECS={env_val!r}, ignoring.", YELLOW)
    log(
        "OFFSET",
        f"Applying global lyrics offset {cli_offset:+.3f}s (neg=sooner, pos=later)",
        CYAN,
    )
    return cli_offset


def find_audio(slug: str, profile: str) -> Path:
    # Prefer rendered mix
    candidates = [
        MIXES_DIR / f"{slug}_{profile}.wav",
        MIXES_DIR / f"{slug}_{profile}.mp3",
        MP3_DIR / f"{slug}.mp3",
    ]
    for c in candidates:
        if c.exists():
            log("AUDIO", f"Using audio: {c}", GREEN)
            return c
    raise SystemExit(
        f"No audio found. Tried: {', '.join(str(c) for c in candidates)}"
    )


def find_timings_csv(slug: str, profile: str) -> Path:
    # First try slug_profile.csv, then slug.csv
    candidates = [
        TIMINGS_DIR / f"{slug}_{profile}.csv",
        TIMINGS_DIR / f"{slug}.csv",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise SystemExit(
        f"No timings CSV found. Tried: {', '.join(str(c) for c in candidates)}"
    )


def load_meta(slug: str) -> dict:
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        log("META", f"Failed to read {meta_path}: {e}", YELLOW)
        return {}


def load_timings(csv_path: Path) -> List[dict]:
    rows: List[dict] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Expect at least: line_index, time_secs, text
        if "time_secs" not in reader.fieldnames or "text" not in reader.fieldnames:
            raise SystemExit(
                f"Timings CSV {csv_path} missing required columns 'time_secs'/'text'"
            )
        for row in reader:
            try:
                t = float(row["time_secs"])
            except ValueError:
                continue
            text = row["text"].strip()
            rows.append({"time_secs": t, "text": text})
    rows.sort(key=lambda r: r["time_secs"])
    return rows


def build_events(rows: List[dict], offset: float) -> List[Event]:
    if not rows:
        raise SystemExit("No timing rows loaded.")

    # Build lyric events from start times
    lyric_events: List[Event] = []
    n = len(rows)
    for i, row in enumerate(rows):
        start = rows[i]["time_secs"] + offset
        if i < n - 1:
            next_start = rows[i + 1]["time_secs"] + offset
            end = next_start - LYRIC_FUDGE_BEFORE_NEXT
        else:
            # Last line: give it a default tail
            end = rows[i]["time_secs"] + offset + 3.0

        # Ensure minimum duration
        if end < start + LYRIC_MIN_DURATION:
            end = start + LYRIC_MIN_DURATION

        # Clamp to >= 0
        if end < 0:
            continue
        if start < 0:
            start = 0.0

        lyric_events.append(
            Event(
                start=start,
                end=end,
                text=row["text"],
                kind="lyric",
            )
        )

    # Insert note events in big gaps between lyric events
    all_events: List[Event] = []
    for i, ev in enumerate(lyric_events):
        all_events.append(ev)
        if i < len(lyric_events) - 1:
            cur = ev
            nxt = lyric_events[i + 1]
            gap = nxt.start - cur.end
            if gap >= NOTE_GAP_THRESHOLD:
                ns = cur.end + NOTE_INSET
                ne = nxt.start - NOTE_INSET
                if ne > ns + 0.5:
                    # Use a small variety of note glyphs so it's not boring
                    # (we just pick one deterministically based on index)
                    note_choices = ["♪", "♫", "♬"]
                    note_char = note_choices[i % len(note_choices)]
                    all_events.append(
                        Event(
                            start=ns,
                            end=ne,
                            text=note_char,
                            kind="note",
                        )
                    )

    all_events.sort(key=lambda e: e.start)
    return all_events


def ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    total_cs = int(round(t * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def escape_ass_text(text: str) -> str:
    # Minimal escaping: newlines and curly braces
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", r"\N")
    )


def write_ass(
    ass_path: Path,
    events: List[Event],
    font_name: str,
    font_size: int,
    slug: str,
) -> None:
    meta = load_meta(slug)
    artist = meta.get("artist", "") or ""
    title = meta.get("title", "") or slug
    full_title = f"{artist} - {title}" if artist else title

    with ass_path.open("w", encoding="utf-8") as f:
        f.write(
            "[Script Info]\n"
            f"Title: {escape_ass_text(full_title)}\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {WIDTH}\n"
            f"PlayResY: {HEIGHT}\n"
            "WrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            # Title centered near top
            f"Style: Title,{font_name},{int(font_size*0.7)},&H00FFFFFF,&H000000FF,"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,8,60,60,120,1\n"
            # Main lyric big, centered low
            f"Style: MainLyric,{font_name},{font_size},&H00FFFFFF,&H000000FF,"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,200,1\n"
            # Next lyric smaller, bottom band
            f"Style: NextLyric,{font_name},{int(font_size*0.6)},&H00CCCCCC,&H000000FF,"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,60,60,60,1\n"
            # Music note in the middle
            f"Style: Note,{font_name},{int(font_size*0.8)},&H0000FFFF,&H000000FF,"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,5,60,60,40,1\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        # Title card before first event (simple: 0 to min(5s, first_start))
        if events:
            first_start = max(0.0, events[0].start)
            title_end = min(first_start, 5.0) if first_start > 0 else 3.0
            if title_end > 0.5:
                f.write(
                    "Dialogue: 0,"
                    f"{ass_time(0.0)},"
                    f"{ass_time(title_end)},"
                    "Title,,0000,0000,0000,,"
                    f"{escape_ass_text(full_title)}\n"
                )

        # Build a list of lyric-only events for "Next:" previews
        lyric_events = [e for e in events if e.kind == "lyric"]

        for idx, ev in enumerate(events):
            if ev.kind == "lyric":
                # Current line
                f.write(
                    "Dialogue: 0,"
                    f"{ass_time(ev.start)},"
                    f"{ass_time(ev.end)},"
                    "MainLyric,,0000,0000,0000,,"
                    f"{escape_ass_text(ev.text)}\n"
                )

                # Next lyric preview (ignore notes)
                next_lyric_text: Optional[str] = None
                for future in lyric_events:
                    if future.start > ev.start:
                        next_lyric_text = future.text
                        break
                if next_lyric_text:
                    preview = f"Next: {next_lyric_text}"
                    f.write(
                        "Dialogue: 0,"
                        f"{ass_time(ev.start)},"
                        f"{ass_time(ev.end)},"
                        "NextLyric,,0000,0000,0000,,"
                        f"{escape_ass_text(preview)}\n"
                    )

            elif ev.kind == "note":
                f.write(
                    "Dialogue: 0,"
                    f"{ass_time(ev.start)},"
                    f"{ass_time(ev.end)},"
                    "Note,,0000,0000,0000,,"
                    f"{escape_ass_text(ev.text)}\n"
                )

    log("ASS", f"Wrote ASS to {ass_path}", GREEN)


def render_ffmpeg(
    audio_path: Path,
    ass_path: Path,
    out_path: Path,
    force: bool,
    font_name: str,
) -> None:
    if out_path.exists() and not force:
        log("MP4", f"Exists, skipping render: {out_path.name}", YELLOW)
        print(f"\nMP4 already present: {out_path}\n")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ffmpeg: black background video + audio + ASS subtitles
    vf = f"subtitles={ass_path.as_posix()}:force_style='FontName={font_name}'"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=size={WIDTH}x{HEIGHT}:rate={FPS}:color=black",
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-vf",
        vf,
        str(out_path),
    ]

    log("MP4", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)
    log("MP4", f"Rendered MP4: {out_path}", GREEN)


def post_ui(out_path: Path) -> None:
    print(f"\nMP4 is at: {out_path}")
    print("What would you like to open?")
    print("  1 = output directory")
    print("  2 = MP4 file")
    print("  3 = both (dir then MP4)")
    print("  0 = none")
    try:
        choice = input("Choice [0–3]: ").strip() or "0"
    except EOFError:
        return

    if choice not in {"0", "1", "2", "3"}:
        return

    if choice in {"1", "3"}:
        if sys.platform == "darwin":
            subprocess.run(["open", str(OUTPUT_DIR)], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(OUTPUT_DIR)], check=False)
    if choice in {"2", "3"}:
        if sys.platform == "darwin":
            subprocess.run(["open", str(out_path)], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(out_path)], check=False)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    slug = args.slug
    profile = args.profile
    offset = resolve_offset(args.offset)

    audio_path = find_audio(slug, profile)
    timings_csv = find_timings_csv(slug, profile)
    rows = load_timings(timings_csv)
    events = build_events(rows, offset)
    log("TIMINGS", f"Loaded {len(events)} timing events (lyrics + notes)", GREEN)

    offset_tag = format_offset_tag(offset)
    out_path = OUTPUT_DIR / f"{slug}_{profile}_offset_{offset_tag}.mp4"

    with tempfile.TemporaryDirectory() as tmpdir:
        ass_path = Path(tmpdir) / f"{slug}_{profile}.ass"
        write_ass(
            ass_path=ass_path,
            events=events,
            font_name=args.font_name,
            font_size=args.font_size,
            slug=slug,
        )
        render_ffmpeg(
            audio_path=audio_path,
            ass_path=ass_path,
            out_path=out_path,
            force=args.force,
            font_name=args.font_name,
        )

    if not args.no_post_ui:
        post_ui(out_path)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
