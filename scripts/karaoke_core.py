#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_core.py – generic helpers, CSV, ASS, timing, YouTube.
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

def colorize(level, msg):
    table = {
        "fatal": f"{RED}💀 [fatal]{RESET}",
        "warn":  f"{YELLOW}⚠️ [warn]{RESET}",
        "info":  f"{CYAN}ℹ️ {RESET}"
    }
    return f"{table.get(level,'')}{msg}"

def info(msg): print(colorize("info", msg))
def warn(msg): print(colorize("warn", msg), file=sys.stderr)
def die(msg, code=1): print(colorize("fatal", msg), file=sys.stderr); sys.exit(code)

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
    """Return duration in seconds using ffprobe."""
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
def sanitize_basename(p: Path) -> str: return re.sub(r"[^A-Za-z0-9_-]+", "_", p.stem).strip("_") or "song"
def yes_no(q): return input(q).strip().lower() == "y"
def read_text_lines(p: Path): return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

def build_arg_parser():
    ap = argparse.ArgumentParser(description="Karaoke Time by Miguel")
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--offset", type=float, default=0)
    ap.add_argument("--font-size", type=int, default=140)
    ap.add_argument("--csv"); ap.add_argument("--ass")
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--resolution", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bg-color", default="black")
    ap.add_argument("--line-hold", type=float, default=2.5)
    ap.add_argument("--model", default="htdemucs_6s")
    ap.add_argument("--device", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-demucs", action="store_true")
    return ap

def tap_to_time(lines: List[str]) -> List[float]:
    print("\n🎤 Manual timing. Press Enter on each line.")
    input("▶ Start playback, then Enter to begin.")
    t0 = time.perf_counter()
    out = []
    for i, line in enumerate(lines, 1):
        print(f"[{i}/{len(lines)}] {line}")
        input("")
        out.append(time.perf_counter() - t0)
    print(f"{GREEN}✅ Timing captured.{RESET}")
    return out

def write_timing_csv(path: Path, lines: List[str], starts: List[float]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["line","start"])
        for ln, st in zip(lines, starts): w.writerow([ln, f"{st:.3f}"])
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
    if t < 0: t = 0
    h, m, s = int(t//3600), int(t%3600//60), int(t%60)
    cs = int((t-int(t))*100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def write_ass(path: Path, w: int, h: int, size: int, lines, starts, offset, hold):
    # Force embed NotoColorEmoji font
    ensure_dir(Path("assets"))
    font_path = Path("assets/NotoColorEmoji.ttf")
    if not font_path.exists():
        info("⬇️ Downloading NotoColorEmoji.ttf for color emoji rendering…")
        run([
            "curl", "-L",
            "-o", str(font_path),
            "https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf"
        ])

    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,NotoColorEmoji,{size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with path.open("w", encoding="utf-8") as f:
        f.write(hdr + "\n")
        for i, line in enumerate(lines):
            st = starts[i] + offset
            en = starts[i + 1] + offset - 0.15 if i < len(lines) - 1 else st + hold
            f.write(f"Dialogue: 0,{srt_time(st)},{srt_time(en)},Default,,0,0,0,,{line}\n")
    info(f"🖋️  Wrote ASS {path}")

def handle_youtube_download(url: str, lyrics_path: Path):
    """Download from YouTube but always use lyrics basename for output folder and file naming."""
    ensure_dir(Path("songs"))
    human_base = sanitize_basename(lyrics_path)
    out_mp3 = Path("songs") / f"{human_base}.mp3"
    if not out_mp3.exists():
        info(f"yt-dlp → {out_mp3}")
        run(["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(out_mp3), url])
        try: subprocess.run(["open", str(out_mp3.parent)])
        except Exception: warn("Couldn't open folder.")
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

# end of karaoke_core.py
