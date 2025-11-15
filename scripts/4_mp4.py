#!/usr/bin/env python3
# scripts/4_mp4.py
#
# Generate a karaoke MP4 from:
#   - audio: mixes/<slug>_<profile>.wav|.mp3 or mp3s/<slug>.mp3
#   - timings: timings/<slug>.csv (line_index,time_secs,text)
#   - lyrics: txts/<slug>.txt (optional for metadata)
#
# Features:
#   - 1920x1080 output
#   - global offset (--offset or KARAOKE_OFFSET_SECS)
#   - ASS overlay with current line + "Next:" preview
#   - output/<slug>_<profile>_offset_<TAG>.mp4
#   - --force to overwrite MP4
#   - --font-size / --font-name
#   - --no-post-ui to skip post-render menu (for 0_master)
#   - Post-render menu (when interactive):
#       1 = open output directory
#       2 = open MP4
#       3 = both
#       4 = upload via scripts/5_upload.py (private)
#
import argparse
import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
META_DIR = BASE_DIR / "meta"
OUTPUT_DIR = BASE_DIR / "output"

WIDTH = 1920
HEIGHT = 1080


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def format_offset_tag(offset: float) -> str:
    """
    Convert numeric offset (seconds) into tag like:
      +0.000 -> p0p000s
      +1.500 -> p1p500s
      -0.500 -> m0p500s
    """
    sign = "p" if offset >= 0 else "m"
    val = abs(offset)
    sec_int = int(val)
    ms_int = int(round((val - sec_int) * 1000))
    return f"{sign}{sec_int}p{ms_int:03d}s"


def load_timings_csv(path: Path):
    """Fallback timings loader if scripts.timings_io is unavailable."""
    events = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "line_index" not in reader.fieldnames or "time_secs" not in reader.fieldnames or "text" not in reader.fieldnames:
            raise ValueError(
                f"Expected CSV header 'line_index,time_secs,text' but got: {reader.fieldnames}"
            )
        for row in reader:
            try:
                idx = int(row["line_index"])
                t = float(row["time_secs"])
                text = row["text"]
            except Exception as e:
                raise ValueError(f"Bad timings row: {row} ({e})")
            events.append((idx, t, text))
    events.sort(key=lambda x: x[1])
    return events


def load_timings_any(slug: str):
    """
    Use scripts.timings_io.load_timings_any if available,
    else fall back to CSV loader.
    """
    csv_path = TIMINGS_DIR / f"{slug}.csv"

    try:
        # Prefer user's shared loader if present.
        from scripts.timings_io import load_timings_any as _load

        return _load(csv_path)
    except Exception:
        # Fallback: simple CSV loader returning list of dicts
        raw = load_timings_csv(csv_path)
        events = []
        for idx, t, text in raw:
            events.append(
                {
                    "line_index": idx,
                    "time_secs": t,
                    "text": text,
                }
            )
        return events


def discover_audio(slug: str, profile: str) -> Path:
    """
    Preferred order:
      1. mixes/<slug>_<profile>.wav
      2. mixes/<slug>_<profile>.mp3
      3. mp3s/<slug>.mp3
    """
    cand1 = MIXES_DIR / f"{slug}_{profile}.wav"
    cand2 = MIXES_DIR / f"{slug}_{profile}.mp3"
    cand3 = MP3_DIR / f"{slug}.mp3"

    for c in (cand1, cand2, cand3):
        if c.exists():
            return c

    raise FileNotFoundError(
        f"No audio found for slug={slug}, profile={profile}. Tried: {cand1}, {cand2}, {cand3}"
    )


def read_meta(slug: str) -> dict:
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return {}
    try:
        import json

        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_ass_content(
    slug: str,
    events: list[dict],
    offset: float,
    font_name: str,
    font_size: int,
) -> str:
    """
    Build an ASS subtitle string with current line + "Next:" preview.
    """
    # Shift times by global offset
    for ev in events:
        ev["time_secs"] = max(0.0, ev["time_secs"] + offset)

    # Compute end times as next start, or last+2s
    for i, ev in enumerate(events):
        if i + 1 < len(events):
            ev["end_secs"] = max(ev["time_secs"] + 0.2, events[i + 1]["time_secs"])
        else:
            ev["end_secs"] = ev["time_secs"] + 2.0

    def srt_time(secs: float) -> str:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = int(secs % 60)
        cs = int(round((secs - int(secs)) * 100))  # centiseconds
        if cs >= 100:
            cs -= 100
            s += 1
        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    # Basic ASS header
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,80,80,80,1
Style: Next,{font_name},{int(font_size*0.7)},&H00CCCCCC,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,2,80,80,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]

    for i, ev in enumerate(events):
        start = srt_time(ev["time_secs"])
        end = srt_time(ev["end_secs"])
        text = ev["text"].replace("\n", "\\N").replace("{", "\\{").replace("}", "\\}")
        # Current line (Main)
        lines.append(
            f"Dialogue: 0,{start},{end},Main,,0000,0000,0000,,{text}\n"
        )

        # Next preview (Next)
        if i + 1 < len(events):
            nxt = events[i + 1]["text"].replace("\n", "\\N").replace("{", "\\{").replace("}", "\\}")
            preview = f"Next: {nxt}"
            lines.append(
                f"Dialogue: 0,{start},{end},Next,,0000,0000,0000,,{preview}\n"
            )

    return "".join(lines)


def build_ffmpeg_cmd(
    audio_path: Path,
    ass_path: Path,
    out_path: Path,
) -> list[str]:
    """
    Build ffmpeg command:
      - black background 1920x1080@30fps
      - audio from audio_path
      - ASS subtitles overlay
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=size={WIDTH}x{HEIGHT}:rate=30:color=black",
        "-i",
        str(audio_path),
        "-vf",
        f"subtitles={ass_path.as_posix()}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_path),
    ]
    return cmd


def open_path(path: Path) -> None:
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", str(path)])
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)])


def prompt_post_ui(slug: str, profile: str, offset: float, mp4_path: Path) -> None:
    print("What would you like to open?")
    print("  1 = output directory")
    print("  2 = MP4 file")
    print("  3 = both (dir then MP4)")
    print("  4 = upload to YouTube (private)")
    print("  0 = none")
    choice = input("Choice [0–4]: ").strip() or "0"

    if choice == "1":
        open_path(OUTPUT_DIR)
    elif choice == "2":
        open_path(mp4_path)
    elif choice == "3":
        open_path(OUTPUT_DIR)
        open_path(mp4_path)
    elif choice == "4":
        # Upload via scripts/5_upload.py
        meta = read_meta(slug)
        artist = meta.get("artist") or ""
        title = meta.get("title") or slug.replace("_", " ")
        base = f"{artist} - {title}".strip(" -")
        base_with_space = base + " " if base else ""

        extra = input(
            f'YouTube title suffix to append to "{base_with_space}" (e.g. "(35% Vocals)") [optional]: '
        ).strip()
        final_title = (base_with_space + extra).strip() if extra else base

        if not final_title:
            final_title = mp4_path.stem

        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "5_upload.py"),
            "--file",
            str(mp4_path),
            "--slug",
            slug,
            "--profile",
            profile,
            "--offset",
            str(offset),
            "--title",
            final_title,
            "--privacy",
            "private",
        ]
        log("UPLOAD", " ".join(cmd), BLUE)
        try:
            cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
            out = cp.stdout.strip()
            if out:
                print(out)
        except subprocess.CalledProcessError as e:
            log("UPLOAD", f"Upload failed with code {e.returncode}", RED)


def parse_args():
    p = argparse.ArgumentParser(description="Generate karaoke MP4 from stems+timings.")
    p.add_argument("--slug", type=str, required=True, help="Song slug")
    p.add_argument("--profile", type=str, default="karaoke", help="Mix profile name")
    p.add_argument("--offset", type=float, default=None, help="Global offset (seconds)")
    p.add_argument("--font-size", type=int, default=72, help="Base font size")
    p.add_argument(
        "--font-name",
        type=str,
        default="Arial",
        help="Font name for ASS style",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite MP4 even if it already exists",
    )
    p.add_argument(
        "--no-post-ui",
        action="store_true",
        help="Do not show post-render UI (for 0_master integration)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    slug = slugify(args.slug)
    profile = args.profile

    # Offset: CLI > env > 0
    if args.offset is not None:
        offset = args.offset
    else:
        env_val = os.getenv("KARAOKE_OFFSET_SECS")
        if env_val:
            try:
                offset = float(env_val)
            except ValueError:
                offset = 0.0
        else:
            offset = 0.0

    log("OFFSET", f"Applying global lyrics offset {offset:+.3f}s", CYAN)

    # Ensure dirs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Audio
    audio_path = discover_audio(slug, profile)
    log("AUDIO", f"Using audio: {audio_path}", GREEN)

    # Timings
    events = load_timings_any(slug)
    if not events:
        raise SystemExit(f"No timing events loaded for slug={slug}")

    log("TIMINGS", f"Loaded {len(events)} timing events", GREEN)

    # Build ASS
    ass_text = build_ass_content(slug, events, offset, args.font_name, args.font_size)

    with tempfile.TemporaryDirectory() as tmpdir:
        ass_path = Path(tmpdir) / f"{slug}_{profile}.ass"
        ass_path.write_text(ass_text, encoding="utf-8")
        log("ASS", f"Wrote ASS to {ass_path}", GREEN)

        # Output file
        tag = format_offset_tag(offset)
        out_path = OUTPUT_DIR / f"{slug}_{profile}_offset_{tag}.mp4"

        if out_path.exists() and not args.force:
            log("MP4", f"Exists, skipping render: {out_path.name}", YELLOW)
            print(f"\nMP4 already present: {out_path}")
            if not args.no_post_ui:
                prompt_post_ui(slug, profile, offset, out_path)
            return

        cmd = build_ffmpeg_cmd(audio_path, ass_path, out_path)
        log("MP4", " ".join(cmd), BLUE)
        subprocess.run(cmd, check=True)
        log("MP4", f"MP4 generation complete: {out_path}", GREEN)

    if not args.no_post_ui:
        prompt_post_ui(slug, profile, offset, out_path)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
