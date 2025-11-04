#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_core.py – generic helpers, CSV, ASS, timing, YouTube, etc.
Plain mode: every line is a lyric line. No special intro handling.
"""

import argparse, csv, re, subprocess, sys, time
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass

# ANSI colors
RESET, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN = (
    "\033[0m", "\033[31m", "\033[32m", "\033[33m",
    "\033[34m", "\033[35m", "\033[36m"
)

def _c(level, msg):
    table = {
        "fatal": f"{RED}💀 [fatal]{RESET}",
        "warn":  f"{YELLOW}⚠️ [warn]{RESET}",
        "info":  f"{CYAN}ℹ️ {RESET}"
    }
    return f"{table.get(level,'')}{msg}"

def info(msg): print(_c("info", msg))
def warn(msg): print(_c("warn", msg), file=sys.stderr)
def die(msg, code=1): print(_c("fatal", msg), file=sys.stderr); sys.exit(code)

def run(cmd, check=True, capture=False):
    printable = " ".join(map(str, cmd))
    info(f"$ {printable}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)

def has_bin(name):
    from shutil import which
    return which(name) is not None

def ensure_bins(require_demucs=True):
    if not has_bin("ffmpeg"): die("ffmpeg not found.")
    if not has_bin("ffprobe"): die("ffprobe not found.")
    if require_demucs and not has_bin("demucs"):
        die("demucs not found. pip3 install demucs")

def audio_duration_seconds(audio_path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(audio_path)],
            capture_output=True, text=True, check=True
        )
        return float(result.stdout.strip())
    except Exception:
        warn(f"⚠️ Could not read duration for {audio_path}, defaulting to 180s")
        return 180.0

def ensure_dir(p: Path): p.mkdir(parents=True, exist_ok=True)
def sanitize_basename(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", p.stem).strip("_") or "song"

def yes_no(q): return input(q).strip().lower() == "y"

def read_text_lines(p: Path):
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

# literal ASS newline
ASS_NL = "\\N"

def slash_to_ass_newlines(text: str) -> str:
    # replace any run of / with ASS newline literal
    return re.sub(r"/+", lambda _: ASS_NL, text)

def build_arg_parser():
    ap = argparse.ArgumentParser(description="Karaoke Time by Miguel")
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--offset", type=float, default=0)
    ap.add_argument("--font-size", type=int, default=140)
    ap.add_argument("--csv")
    ap.add_argument("--ass")
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--resolution", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bg-color", default="black")
    ap.add_argument("--line-hold", type=float, default=2.5)
    ap.add_argument("--model", default="htdemucs_6s")
    ap.add_argument("--device", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-demucs", action="store_true")
    ap.add_argument("--chrome-static-slides", action="store_true",
                    help="Render emoji-safe slides with Chromium and stitch to mp4.")
    ap.add_argument("--chrome-font-size", type=int, default=100,
                    help="Font size for chrome-static-slides (px).")
    return ap

def tap_to_time(lines: List[str]) -> List[float]:
    print("\n🎤 Manual timing. Press Enter on each line.")
    input("▶ Start playback, then press Enter to start timing.")
    out = []
    t0 = None
    for i, line in enumerate(lines, 1):
        print(f"[{i}/{len(lines)}] {line}")
        input("")
        now = time.perf_counter()
        if t0 is None:
            t0 = now
        out.append(now - t0)
    print(f"{GREEN}✅ Timing captured.{RESET}")
    return out

def write_timing_csv(path: Path, lines: List[str], starts: List[float]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line","start"])
        for ln, st in zip(lines, starts):
            w.writerow([ln, f"{st:.3f}"])
    info(f"📝 Saved CSV {path}")

def read_timing_csv(path: Path) -> Tuple[List[str], List[float]]:
    lines, starts = [], []
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            lines.append(row["line"])
            starts.append(float(row["start"]))
    return lines, starts

def srt_time(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def write_ass(path: Path, w: int, h: int, size: int, lines, starts, offset, hold):
    """
    Simple ASS writer:
    - all lines treated equally
    - vertically centered
    - "/" → ASS newline
    - pad timings if needed
    """
    import sys as _sys
    if _sys.platform == "darwin":
        font = "Apple Color Emoji"
    elif _sys.platform.startswith("win"):
        font = "Segoe UI Emoji"
    else:
        font = "Noto Color Emoji"

    safe_starts = list(starts)
    if len(safe_starts) == 0:
        safe_starts = [0.0]
    if len(safe_starts) < len(lines):
        last = safe_starts[-1]
        for _ in range(len(lines) - len(safe_starts)):
            last += hold
            safe_starts.append(last)

    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,1,3,0,5,10,10,20,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with path.open("w", encoding="utf-8") as f:
        f.write(hdr)
        for i, line in enumerate(lines):
            st = safe_starts[i] + offset
            if i < len(lines) - 1:
                en = safe_starts[i + 1] + offset - 0.15
                if en <= st:
                    en = st + 0.15
            else:
                en = st + hold
            line_txt = slash_to_ass_newlines(line)
            f.write(
                f"Dialogue: 0,{srt_time(st)},{srt_time(en)},Default,,0,0,0,,{line_txt}\n"
            )
    info(f"🖋️  Wrote ASS {path}")

def handle_youtube_download(url: str, lyrics_path: Path):
    ensure_dir(Path("songs"))
    human_base = sanitize_basename(lyrics_path)
    out_mp3 = Path("songs") / f"{human_base}.mp3"
    if not out_mp3.exists():
        info(f"yt-dlp → {out_mp3}")
        run(["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(out_mp3), url])
        try:
            subprocess.run(["open", str(out_mp3.parent)])
        except Exception:
            warn("Couldn't open folder.")
    else:
        info(f"Reusing {out_mp3}")
    out_dir = Path("output") / human_base
    ensure_dir(out_dir)
    return out_mp3, human_base, out_dir

@dataclass
class StemPlan:
    selected: Dict[str,int]
    all_levels: Dict[str,int]

STEM_ORDER = ["bass","drums","guitar","other","piano","vocals"]
STEM_MENU = [(n.title(), n) for n in STEM_ORDER]

def print_plan_summary(lyrics, audio, out_dir, csv, ass, final, plan, target):
    print(f"\n{BLUE}===== PLAN ====={RESET}")
    print(f"Lyrics: {lyrics}\nAudio: {audio}\nOut: {out_dir}\nCSV: {csv}\nASS: {ass}\nMP4: {final}")
    print("Stems:")
    for label, key in STEM_MENU:
        print(f"  {label:<10}{plan.all_levels[key]:>4}%")
    print(f"Mix target: {target}")
    print("================\n")

def song_base_from_path(lyrics_path: Path) -> str:
    return sanitize_basename(lyrics_path)

def stitch_frames_to_mp4(frames_glob: str,
                         audio_path: Path,
                         out_mp4_path: Path,
                         fps_visual: int = 30,
                         seconds_per_frame: float = 1.5):
    ensure_dir(out_mp4_path.parent)
    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"1/{seconds_per_frame}",
        "-pattern_type", "glob", "-i", frames_glob,
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-r", str(fps_visual),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_mp4_path),
    ]
    run(cmd, check=True)
    info(f"🎬 Chrome static video → {out_mp4_path}")
# end of karaoke_core.py
