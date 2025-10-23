#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_by_miguel.py
All-in-one local tool:
1) Optional interactive 6-stem "Perform Along Buddy" mixer (Demucs htdemucs_6s)
2) Manual tap-to-time loop to create a timing CSV (skipped if CSV provided or exists)
3) ASS subtitle generation (skipped if ASS provided or exists)
4) Final MP4 render with subtitles over a generated background

Defaults:
  --offset -2
  --font-size 140
  interactive by default (no --no-prompt flag)

Requirements:
  - ffmpeg, ffprobe in PATH
  - demucs CLI in PATH (pip3 install demucs) or callable module
  - Python libs: soundfile, tqdm (in requirements.txt)

Notes:
  - If --audio is missing, prints a ready-to-copy yt-dlp command to fetch mp3.
  - Output layout: output/<base>/<base>_{instrumental|buddy_mix|timing.csv|subtitles.ass|karaoke[buddy].mp4}
  - For stem mixing: only stems with volume != 100% are altered; 100% are passed-through without extra filters.
  - If no stems are selected for modification, you can still generate a plain instrumental by setting vocals=0 in the selection step (or skip selection to keep full mix).
"""

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 🎨 ANSI Color Codes (added)
RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

def colorize(level: str, msg: str) -> str:
    """Color-coded messages for better UX."""
    if level == "fatal":
        return f"{RED}💀 [fatal]{RESET} {msg}"
    if level == "warn":
        return f"{YELLOW}⚠️  [warn]{RESET} {msg}"
    if level == "info":
        return f"{CYAN}ℹ️  {msg}{RESET}"
    return msg

try:
    import soundfile as sf
except Exception:
    sf = None  # audio duration fallback will use ffprobe

# ------------------------------- Utils ---------------------------------- #

def die(msg: str, code: int = 1):
    print(colorize("fatal", msg), file=sys.stderr)
    sys.exit(code)

def warn(msg: str):
    print(colorize("warn", msg), file=sys.stderr)

def info(msg: str):
    print(colorize("info", msg))

def run(cmd: List[str], check: bool = True, capture: bool = False, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a subprocess with nice logging."""
    printable = " ".join(shlex.quote(x) for x in cmd)
    info(f"$ {printable}")
    return subprocess.run(cmd, check=check, cwd=cwd, capture_output=capture, text=True)

def has_bin(name: str) -> bool:
    from shutil import which
    return which(name) is not None

def ensure_bins():
    if not has_bin("ffmpeg"): die("ffmpeg not found in PATH ⚙️")
    if not has_bin("ffprobe"): die("ffprobe not found in PATH ⚙️")
    if not has_bin("demucs"):
        warn("demucs not found in PATH. Install with: pip3 install demucs")
        die("demucs is required for stem separation 🎚️")

def read_text_lines(path: Path) -> List[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Normalize CRLF and strip trailing spaces. Keep empty lines? Typically skip.
    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    # Remove leading/trailing empty runs but keep meaningful blanks if any:
    return [ln for ln in lines if ln != ""]

def audio_duration_seconds(audio_path: Path) -> float:
    """Return duration in seconds using soundfile or ffprobe."""
    if sf is not None:
        try:
            with sf.SoundFile(str(audio_path)) as f:
                return float(len(f) / f.samplerate)
        except Exception:
            pass
    # fallback to ffprobe
    try:
        result = run([
            "ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", str(audio_path)
        ], check=True, capture=True)
        dur = float(result.stdout.strip())
        return dur
    except Exception:
        warn("Unable to read duration; defaulting to 180s ⏱️")
        return 180.0

def time_s() -> float:
    return time.perf_counter()

def sanitize_basename(p: Path) -> str:
    base = p.stem
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", base).strip("_")
    return base or "song"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def yes_no(prompt: str, default_no: bool = True) -> bool:
    ans = input(f"{YELLOW}{prompt}{RESET}").strip().lower()
    if ans == "y": return True
    if ans == "n": return False
    return not default_no

# ---------------------------- CLI Arguments ------------------------------ #

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="🎶 Karaoke Time by Miguel — all-in-one local tool with Perform Along Buddy mode",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--lyrics", required=True, help="Path to lyrics .txt (one line per subtitle line)")
    ap.add_argument("--audio", required=True, help="Path to input song .mp3")
    ap.add_argument("--offset", type=float, default=0, help="Global seconds added to each cue start (negative to shift earlier)")
    ap.add_argument("--font-size", type=int, default=140, help="ASS subtitle font size")
    ap.add_argument("--csv", help="Optional existing timing CSV to reuse")
    ap.add_argument("--ass", help="Optional existing ASS file to reuse")
    ap.add_argument("--no-prompt", action="store_true", help="Run in non-interactive mode where possible")
    ap.add_argument("--resolution", default="1280x720", help="Output video resolution WxH")
    ap.add_argument("--fps", type=int, default=30, help="Output video FPS")
    ap.add_argument("--bg-color", default="black", help="Background color for generated video (ffmpeg color)")
    ap.add_argument("--line-hold", type=float, default=2.5, help="Default hold duration for last line or single line")
    ap.add_argument("--model", default="htdemucs_6s", help="Demucs model name")
    ap.add_argument("--device", default=None, help="Demucs device (cpu/cuda). Default auto.")
    ap.add_argument("--dry-run", action="store_true", help="Compute plan, print steps, do nothing")
    return ap

# -------------------------- Tap-to-time Capture -------------------------- #

def tap_to_time(lines: List[str], interactive: bool = True) -> List[float]:
    """Return start times (seconds) per line based on Enter key taps."""
    print(f"\n{MAGENTA}🎤 Manual lyric timing mode activated!{RESET}")
    print("Press Enter when each line should APPEAR. Press Ctrl+C to abort.\n")
    if not interactive:
        die("Interactive timing requested but --no-prompt passed or no TTY ❌")

    # Allow user to get ready
    input("🎵 Cue ready. Start your song playback now, then press Enter to begin...")
    start = time_s()
    starts: List[float] = []
    try:
        for i, line in enumerate(lines, 1):
            print(f"[{i:>3}/{len(lines)}] {line}")
            input("")  # press Enter in sync
            t = time_s() - start
            starts.append(t)
    except KeyboardInterrupt:
        die("Timing interrupted by user ❌")
    print(f"{GREEN}✅ Timing complete!{RESET}\n")
    return starts

# ----------------------------- CSV Handling ------------------------------ #

def write_timing_csv(csv_path: Path, lines: List[str], starts: List[float]):
    if len(lines) != len(starts):
        die(f"Line count ({len(lines)}) != tap count ({len(starts)})")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line","start"])
        for ln, st in zip(lines, starts):
            w.writerow([ln, f"{st:.3f}"])
    info(f"📝 Saved timing CSV → {csv_path}")

def read_timing_csv(csv_path: Path) -> Tuple[List[str], List[float]]:
    lines, starts = [], []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if "line" not in r.fieldnames or "start" not in r.fieldnames:
            die("Timing CSV missing required headers: line,start ⚠️")
        for row in r:
            lines.append(row["line"])
            starts.append(float(row["start"]))
    info(f"📂 Loaded timing CSV → {csv_path}")
    return lines, starts

# ---------------------------- ASS Generation ----------------------------- #

ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,5,50,50,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".rstrip()

def srt_time(t: float) -> str:
    if t < 0: t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t - int(t)) * 100)  # centiseconds for ASS
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

def write_ass(ass_path: Path, w: int, h: int, font_size: int,
              lines: List[str], starts: List[float], offset: float,
              default_hold: float):
    n = len(lines)
    # Derive end times: next start - small epsilon; last = start + default_hold
    ends: List[float] = []
    for i in range(n):
        st = starts[i] + offset
        if i < n-1:
            en = starts[i+1] + offset - 0.15
            if en <= st: en = st + 0.15
        else:
            en = st + max(default_hold, 0.5)
        ends.append(en)

    header = ASS_HEADER_TEMPLATE.format(w=w, h=h, font_size=font_size)
    with ass_path.open("w", encoding="utf-8") as f:
        f.write(header + "\n")
        for line, st, en in zip(lines, starts, ends):
            txt = line.replace("\\N", r"\N")
            f.write(f"Dialogue: 0,{srt_time(st+offset)},{srt_time(en)},Default,,0,0,0,,{txt}\n")
    info(f"🖋️  Wrote new ASS subtitles → {ass_path}")

# ---------------------------- Demucs + Mix ------------------------------- #

STEM_ORDER = ["bass","drums","guitar","other","piano","vocals"]
STEM_MENU = [
    ("Bass","bass"),
    ("Drums","drums"),
    ("Guitar","guitar"),
    ("Other","other"),
    ("Piano","piano"),
    ("Vocals","vocals"),
]

@dataclass
class StemPlan:
    selected: Dict[str, int]  # stem -> percent (0..100)
    all_levels: Dict[str, int]  # all stems explicit 0..100

def interactive_stem_selection(no_prompt: bool) -> StemPlan:
    if no_prompt:
        levels = {name: 100 for name in STEM_ORDER}
        return StemPlan(selected={}, all_levels=levels)

    chosen = set()
    while True:
        print(f"\n{MAGENTA}🎚️  Select stems to modify. Enter 1-6 to toggle. 0 to confirm.{RESET}")
        for idx, (label, key) in enumerate(STEM_MENU, 1):
            mark = "x" if key in chosen else " "
            print(f"{idx} - [{mark}] {label}")
        sel = input("> ").strip()
        if sel == "0":
            break
        if sel in [str(i) for i in range(1,7)]:
            idx = int(sel) - 1
            key = STEM_MENU[idx][1]
            if key in chosen: chosen.remove(key)
            else: chosen.add(key)
        else:
            print("Enter a number 0..6 🎵")

    if not chosen:
        levels = {name: 100 for name in STEM_ORDER}
        return StemPlan(selected={}, all_levels=levels)

    names = [label for (label,key) in STEM_MENU if key in chosen]
    yn = input(f"🎛️  You selected: {', '.join(names)} - [y/N] to confirm: ").strip().lower()
    if yn != "y":
        return interactive_stem_selection(no_prompt=False)

    levels = {name: 100 for name in STEM_ORDER}
    for label, key in STEM_MENU:
        if key in chosen:
            while True:
                val = input(f"Enter percent for: {label} (0-100) 🎚️ : ").strip()
                if not val:
                    pct = 100
                    break
                if val.isdigit() and 0 <= int(val) <= 100:
                    pct = int(val)
                    break
                print("Enter integer 0..100.")
            levels[key] = pct

    print(f"\n{CYAN}Summary of requested stem levels:{RESET}")
    print("Stem       Volume %")
    print("-------------------")
    for label, key in STEM_MENU:
        print(f"{label:<10} {levels[key]:>6}")
    yn2 = input("Proceed with Demucs processing and mixing? [y/N]: ").strip().lower()
    if yn2 != "y":
        return interactive_stem_selection(no_prompt=False)

    selected = {k: levels[k] for k in chosen}
    return StemPlan(selected=selected, all_levels=levels)

def run_demucs_separation(audio_path: Path, model: str, device: Optional[str], out_root: Path) -> Path:
    ensure_dir(out_root)
    base = sanitize_basename(audio_path)
    demucs_dir = out_root / model / base

    if all((demucs_dir / f"{stem}.wav").exists() for stem in STEM_ORDER):
        info(f"♻️  Reusing existing stems in {demucs_dir}")
        return demucs_dir

    info("🎵 Running Demucs separation (stems not found)...")
    cmd = ["demucs", "--name", model, "--out", str(out_root)]
    if device:
        cmd += ["--device", device]
    cmd += [str(audio_path)]
    run(cmd, check=True)
    return demucs_dir

def mix_stems_to_file(stem_dir: Path, levels: Dict[str, int], out_mp3: Path):
    inputs, filters, map_names = [], [], []
    idx = 0

    for stem in STEM_ORDER:
        stem_wav = stem_dir / f"{stem}.wav"
        if not stem_wav.exists():
            die(f"Missing stem file: {stem_wav}")
        inputs += ["-i", str(stem_wav)]
        level = levels.get(stem, 100)
        if level == 100:
            map_names.append(f"[{idx}:a]")
        else:
            vol = max(level, 0) / 100.0
            filters.append(f"[{idx}:a]volume={vol}[a{idx}]")
            map_names.append(f"[a{idx}]")
        idx += 1

    fc = ""
    if filters:
        fc += ";".join(filters) + ";"
    fc += f"{''.join(map_names)}amix=inputs=6:normalize=0[aout]"

    info("🎚️  Mixing stems into final accompaniment...")
    cmd = ["ffmpeg","-y"] + inputs + ["-filter_complex", fc, "-map","[aout]","-c:a","libmp3lame","-q:a","2", str(out_mp3)]
    run(cmd, check=True)
    info(f"🎧 Mixed audio → {out_mp3}")

# ----------------------------- Rendering -------------------------------- #

def render_karaoke_video(audio_path: Path, ass_path: Path, out_mp4: Path,
                         resolution: str, fps: int, bg_color: str):
    dur = audio_duration_seconds(audio_path)
    w_h = resolution
    tmp_video = out_mp4.with_suffix(".tmp_video.mp4")

    subfilter = f"ass={ass_path.as_posix()}"
    info("🎬 Rendering video background and burning subtitles...")
    cmd1 = [
        "ffmpeg","-y",
        "-f","lavfi","-t", f"{dur:.3f}",
        "-i", f"color=c={bg_color}:s={w_h}:r={fps}",
        "-vf", subfilter,
        "-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
        str(tmp_video)
    ]
    run(cmd1, check=True)

    cmd2 = [
        "ffmpeg","-y",
        "-i", str(tmp_video),
        "-i", str(audio_path),
        "-c:v","copy",
        "-c:a","aac","-b:a","192k",
        "-shortest",
        "-movflags","+faststart",
        str(out_mp4)
    ]
    run(cmd2, check=True)
    try:
        tmp_video.unlink()
    except Exception:
        pass
    info(f"📽️  Final video ready → {out_mp4}")

# ------------------------------ Main Flow -------------------------------- #

def print_ytdlp_hint(target_mp3: Path):
    base = target_mp3.name
    print("\n🎧 Audio file not found.")
    print("💡 To fetch an MP3 from YouTube, run:")
    print(f'yt-dlp -x --audio-format mp3 "<YouTube_URL>" -o "{base}"\n')

def main():
    ap = build_arg_parser()
    args = ap.parse_args()

    lyrics_path = Path(args.lyrics).expanduser().resolve()
    audio_path = Path(args.audio).expanduser().resolve()
    if not lyrics_path.exists():
        die(f"Lyrics file not found: {lyrics_path}")

    base = sanitize_basename(audio_path)
    out_dir = Path("output") / base
    ensure_dir(out_dir)

    csv_path = Path(args.csv).expanduser().resolve() if args.csv else (out_dir / f"{base}_timing.csv")
    ass_path = Path(args.ass).expanduser().resolve() if args.ass else (out_dir / f"{base}_subtitles.ass")
    buddy_mp3 = out_dir / f"{base}_buddy_mix.mp3"
    instr_mp3 = out_dir / f"{base}_instrumental.mp3"
    final_mp4  = out_dir / f"{base}_karaoke.mp4"

    if not audio_path.exists():
        print_ytdlp_hint(audio_path)
        die(f"Audio mp3 not found: {audio_path}")

    ensure_bins()

    stem_plan = interactive_stem_selection(no_prompt=args.no_prompt)

    print(f"\n{BLUE}===== PLAN SUMMARY ====={RESET}")
    print(f"Lyrics: {lyrics_path}")
    print(f"Audio : {audio_path}")
    print(f"Output dir: {out_dir}")
    print("\nStem levels (percent):")
    for label, key in STEM_MENU:
        print(f"  {label:<10} {stem_plan.all_levels[key]:>3}%")
    any_change = any(v != 100 for v in stem_plan.all_levels.values())
    target_audio = buddy_mp3 if any_change else instr_mp3
    final_name = out_dir / (f"{base}_karaoke_buddy.mp4" if any_change else f"{base}_karaoke.mp4")
    print(f"\n🎵 Accompaniment target: {target_audio.name}")
    print(f"🎬 Final video target  : {final_name.name}")
    print("========================\n")

    if not args.no_prompt:
        proceed = yes_no("Proceed with Demucs separation and audio mixing? [y/N]: ", default_no=True)
        if not proceed:
            die("Cancelled by user before Demucs ❌")

    if args.dry_run:
        print("[dry-run] Exiting before processing.")
        return

    demucs_out_root = out_dir / "demucs_stems"
    demucs_dir = run_demucs_separation(audio_path, args.model, args.device, demucs_out_root)

    levels = dict(stem_plan.all_levels)
    if not any_change:
        levels["vocals"] = 0

    info("🎛️  Beginning stem mixdown process...")
    target_audio = buddy_mp3 if any_change else instr_mp3
    mix_stems_to_file(demucs_dir, levels, target_audio)

    if csv_path.exists():
        info(f"🕒 Reusing timing CSV: {csv_path}")
        lines, starts = read_timing_csv(csv_path)
    else:
        lines = read_text_lines(lyrics_path)
        if not lines:
            die("Lyrics file has no lines after trimming.")
        if args.no_prompt:
            die("Timing CSV missing and --no-prompt set; cannot capture interactively.")
        starts = tap_to_time(lines, interactive=True)
        write_timing_csv(csv_path, lines, starts)
        info(f"💾 Saved timing CSV: {csv_path}")

    m = re.match(r"^(\d+)x(\d+)$", args.resolution.strip().lower())
    if not m:
        die(f"Bad --resolution: {args.resolution}")
    w, h = int(m.group(1)), int(m.group(2))
    if 'lines' not in locals() or 'starts' not in locals():
        lines, starts = read_timing_csv(csv_path)
    write_ass(ass_path, w, h, args.font_size, lines, starts, args.offset, args.line_hold)
    info(f"🖋️  Overwrote ASS: {ass_path}")

    info("🎬 Rendering final MP4 with subtitles...")
    render_karaoke_video(target_audio, ass_path, final_name, args.resolution, args.fps, args.bg_color)
    info(f"{GREEN}✅ Done. Output: {final_name}{RESET}")
    print(f"{MAGENTA}🎉 Enjoy your karaoke video! 🎶{RESET}")

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print("\n[error] A subprocess failed.", file=sys.stderr)
        if e.stdout: print(e.stdout, file=sys.stderr)
        if e.stderr: print(e.stderr, file=sys.stderr)
        sys.exit(e.returncode)
    except Exception as ex:
        print(f"\n[error] {ex}", file=sys.stderr)
        sys.exit(1)

# end of karaoke_time_by_miguel.py
