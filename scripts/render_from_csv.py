#!/usr/bin/env python3
import argparse
import csv
import os
import random
import shlex
import subprocess
import sys
import tempfile
from typing import List, Dict, Any, Optional

# We keep the random choices stable per-run only if you want; for now, leave it real random.
FILLER_SYMBOLS = ["♫", "♪", "♬", "♩"]

def read_csv_lines(csv_path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # be resilient to extra cols after "start"
            text = row.get("line", "").strip()
            start = float(row.get("start", "0") or 0)
            end = row.get("end")
            if end is None or end == "":
                end = start + 1.0
            else:
                end = float(end)
            rows.append({"line": text, "start": start, "end": end})
    # sort by start, just in case
    rows.sort(key=lambda r: r["start"])
    return rows


def ffprobe_duration(audio_path: str) -> Optional[float]:
    try:
      # ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 file
      cmd = [
          "ffprobe",
          "-v", "error",
          "-show_entries", "format=duration",
          "-of", "default=noprint_wrappers=1:nokey=1",
          audio_path,
      ]
      out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
      return float(out)
    except Exception:
      return None


def seconds_to_ass_time(t: float) -> str:
    # ASS wants h:mm:ss.cs (centiseconds)
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    # centiseconds:
    cs = int(round((s - int(s)) * 100))
    return f"{h:d}:{m:02d}:{int(s):02d}.{cs:02d}"


def wrap_line(text: str, max_chars: int) -> List[str]:
    if max_chars <= 0:
        return [text]
    words = text.split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def build_ass(
    events: List[Dict[str, Any]],
    ass_path: str,
    font_size: int,
    hpad_pct: int,
    valign: str,
    video_w: int,
    video_h: int,
    artist: Optional[str],
    title: Optional[str],
):
    # valign: "top" -> 8, "middle" -> 5, "bottom" -> 2
    valign_map = {
        "top": 8,
        "middle": 5,
        "center": 5,
        "bottom": 2,
    }
    alignment = valign_map.get(valign.lower(), 5)

    # horizontal padding: we turn percentage into margins
    # libass margins are in pixels
    margin_l = int(video_w * (hpad_pct / 100.0))
    margin_r = int(video_w * (hpad_pct / 100.0))

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\n")
        f.write("ScriptType: v4.00+\n")
        f.write("PlayResX: 1280\n")
        f.write("PlayResY: 720\n")
        f.write("WrapStyle: 2\n")
        f.write("ScaledBorderAndShadow: yes\n")
        f.write("\n")
        f.write("[V4+ Styles]\n")
        f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding\n")
        # We keep it simple and bright. You can tweak later.
        f.write(
            f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
            f"0,0,0,0,100,100,0,0,1,3,0,{alignment},{margin_l},{margin_r},40,1\n"
        )
        f.write("\n")
        f.write("[Events]\n")
        f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

        for ev in events:
            start_ass = seconds_to_ass_time(ev["start"])
            end_ass = seconds_to_ass_time(ev["end"])
            txt = ev["text"].replace("\n", "\\N")
            f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{txt}\n")


def main():
    parser = argparse.ArgumentParser(description="CSV (line,start,end) → ASS → ffmpeg → MP4")
    parser.add_argument("--csv", required=True, help="CSV with line,start,end")
    parser.add_argument("--audio", required=True, help="Audio file (mp3/wav)")
    parser.add_argument("--font-size", type=int, default=50, help="Base font size for lyrics")
    parser.add_argument("--car-font-size", type=int, default=None, help="Optional alt font size (car)")
    parser.add_argument("--repo-root", default=".", help="Project root")
    parser.add_argument("--output-name", default=None, help="Output name (without extension)")
    parser.add_argument("--offset-video", type=float, default=-1.0, help="Global video offset (sec)")
    parser.add_argument("--extra-delay", type=float, default=0.0, help="Extra delay to push lyrics later (sec)")
    parser.add_argument("--hpad-pct", type=int, default=6, help="Horizontal padding as percent of width")
    parser.add_argument("--valign", default="middle", help="Vertical alignment: top|middle|bottom")
    parser.add_argument("--max-chars", type=int, default=18, help="Max chars per line before wrapping")
    parser.add_argument("--artist", default="", help="Artist name for intro screen")
    parser.add_argument("--title", default="", help="Title name for intro screen")
    parser.add_argument("--gap-threshold", type=float, default=5.0, help="Min gap (sec) to insert music notes")
    parser.add_argument("--gap-delay", type=float, default=2.0, help="Delay after prev line ends before music note shows")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--no-open", action="store_true", help="Don't open output afterward")
    args = parser.parse_args()

    csv_path = os.path.abspath(args.csv)
    audio_path = os.path.abspath(args.audio)
    repo_root = os.path.abspath(args.repo_root)
    output_dir = os.path.join(repo_root, "output")
    os.makedirs(output_dir, exist_ok=True)

    base_name = args.output_name
    if not base_name:
        # derive from csv filename
        base_name = os.path.splitext(os.path.basename(csv_path))[0]

    # 1) load CSV
    rows = read_csv_lines(csv_path)
    if not rows:
        print("No rows in CSV.", file=sys.stderr)
        sys.exit(1)

    # 2) determine full duration (from audio)
    audio_dur = ffprobe_duration(audio_path)
    if audio_dur is None:
        # fallback: last lyric end
        audio_dur = rows[-1]["end"]

    # 3) build events (intro + lyrics + gaps)
    events = []

    # intro screen: 0.0 → first_lyric_start
    first_start = rows[0]["start"]
    intro_text_lines = []
    if args.title:
        intro_text_lines.append(args.title)
    if args.artist:
        intro_text_lines.append("by")
        intro_text_lines.append(args.artist)
    intro_text = "\\N".join(intro_text_lines) if intro_text_lines else "Karaoke Time"
    events.append({
        "text": intro_text,
        "start": 0.0,
        "end": first_start if first_start > 0 else 3.0,
    })

    # now actual lyric events
    for r in rows:
        line = r["line"].strip()
        start = r["start"]
        end = r["end"]
        # apply extra-delay globally (lyrics later)
        start = start + args.extra_delay
        end = end + args.extra_delay

        # wrap long lines
        wrapped = wrap_line(line, args.max_chars)
        if len(wrapped) == 1:
            events.append({
                "text": wrapped[0],
                "start": start,
                "end": end,
            })
        else:
            # multi-line block
            block_txt = "\\N".join(wrapped)
            events.append({
                "text": block_txt,
                "start": start,
                "end": end,
            })

    # 4) insert music-note fillers for gaps
    # we look at final events list (intro + lyrics)
    # find pairs of (lyric → next lyric) where gap >= threshold
    # BUT: filler starts at prev_end + gap_delay
    final_events: List[Dict[str, Any]] = []
    for i, ev in enumerate(events):
        final_events.append(ev)
        if i == 0:
            continue
        # current 'ev' is the lyric we just appended,
        # previous lyric is final_events[-2]
    # Actually easier: re-walk the lyric rows only, because intro is 0..first_start
    # We'll create fillers from original CSV rows (with delay applied)

    # start from original lyric rows (with delay applied)
    lyric_events = [e for e in events if e is not events[0]]  # skip intro
    fillers: List[Dict[str, Any]] = []
    for i in range(len(lyric_events) - 1):
        cur = lyric_events[i]
        nxt = lyric_events[i + 1]
        gap = nxt["start"] - cur["end"]
        if gap >= args.gap_threshold:
            filler_start = cur["end"] + args.gap_delay
            # don't collide with next start
            filler_end = nxt["start"] - 0.10
            if filler_start < filler_end:
                symbol = random.choice(FILLER_SYMBOLS)
                fillers.append({
                    "text": symbol,
                    "start": filler_start,
                    "end": filler_end,
                })

    # end-of-song filler
    last_lyric = lyric_events[-1]
    tail_gap = audio_dur - last_lyric["end"]
    if tail_gap >= args.gap_threshold:
        filler_start = last_lyric["end"] + args.gap_delay
        filler_end = audio_dur - 0.10
        if filler_start < filler_end:
            symbol = random.choice(FILLER_SYMBOLS)
            fillers.append({
                "text": symbol,
                "start": filler_start,
                "end": filler_end,
            })

    # merge all events and sort
    all_events = events + fillers
    all_events.sort(key=lambda e: e["start"])

    # 5) write ASS
    with tempfile.TemporaryDirectory() as tmpdir:
        ass_path = os.path.join(tmpdir, "lyrics.ass")
        font_size = args.font_size
        if args.car_font_size is not None:
            font_size = args.car_font_size
        build_ass(
            all_events,
            ass_path,
            font_size=font_size,
            hpad_pct=args.hpad_pct,
            valign=args.valign,
            video_w=args.width,
            video_h=args.height,
            artist=args.artist,
            title=args.title,
        )

        # 6) ffmpeg render
        out_mp4 = os.path.join(output_dir, base_name + ".mp4")
        video_duration = max(audio_dur + args.offset_video, 0.1)

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={args.width}x{args.height}:d={video_duration}",
            "-i", audio_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            out_mp4,
        ]

        print("▶ Rendering to", out_mp4)
        try:
            subprocess.check_call(ffmpeg_cmd)
        except subprocess.CalledProcessError as e:
            print("ffmpeg failed:", e, file=sys.stderr)
            sys.exit(1)

    print("[OK] wrote", out_mp4)
    if not args.no_open:
        if sys.platform == "darwin":
            subprocess.call(["open", out_mp4])
        elif sys.platform.startswith("linux"):
            subprocess.call(["xdg-open", out_mp4])


if __name__ == "__main__":
    main()
# end of render_from_csv.py
