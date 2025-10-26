#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_by_miguel.py
All-in-one local tool:
1) Optional interactive 6-stem "Perform Along Buddy" mixer (Demucs htdemucs_6s)
2) Manual tap-to-time loop to create a timing CSV (skipped if CSV provided or exists)
3) ASS subtitle generation (skipped if ASS provided or exists)
4) Final MP4 render with subtitles over a generated background

CSV contract (required):
    header row: line,start
    each row :  actual text line , float seconds

Defaults:
  --offset -2
  --font-size 140
  interactive by default (no --no-prompt flag)

Requirements:
  - ffmpeg, ffprobe in PATH
  - demucs CLI in PATH (pip3 install demucs) or callable module
  - Python libs: soundfile, tqdm (in requirements.txt)

Notes:
  - If --audio is a YouTube URL, script will try yt-dlp to fetch mp3.
  - Output layout: output/<base>/<base>_{instrumental|buddy_mix|timing.csv|subtitles.ass|karaoke[buddy].mp4}
  - For stem mixing: only stems with volume != 100% are altered; 100% passes straight.
  - If you pass --skip-demucs we do zero stem work and just use the input audio.
"""

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ------------- ANSI Color Codes ------------- #

RESET   = "\033[0m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
CYAN    = "\033[36m"

def colorize(level: str, msg: str) -> str:
    if level == "fatal":
        return f"{RED}💀 [fatal]{RESET} {msg}"
    if level == "warn":
        return f"{YELLOW}⚠️  [warn]{RESET} {msg}"
    if level == "info":
        return f"{CYAN}ℹ️  {msg}{RESET}"
    return msg

def die(msg: str, code: int = 1):
    print(colorize("fatal", msg), file=sys.stderr)
    sys.exit(code)

def warn(msg: str):
    print(colorize("warn", msg), file=sys.stderr)

def info(msg: str):
    print(colorize("info", msg))

# ------------- Optional deps ------------- #

try:
    import soundfile as sf
except Exception:
    sf = None  # fallback to ffprobe for duration

# ------------- Shell helpers ------------- #

def run(cmd: List[str], check: bool = True, capture: bool = False, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    printable = " ".join(shlex.quote(x) for x in cmd)
    info(f"$ {printable}")
    return subprocess.run(
        cmd,
        check=check,
        cwd=cwd,
        capture_output=capture,
        text=True,
    )

def has_bin(name: str) -> bool:
    from shutil import which
    return which(name) is not None

def ensure_bins(require_demucs: bool = True):
    if not has_bin("ffmpeg"):  die("ffmpeg not found in PATH ⚙️")
    if not has_bin("ffprobe"): die("ffprobe not found in PATH ⚙️")
    if require_demucs and not has_bin("demucs"):
        warn("demucs not found in PATH. pip3 install demucs")
        die("demucs is required for stem separation 🎚️")

# ------------- FS helpers ------------- #

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def sanitize_basename(p: Path) -> str:
    base = p.stem
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", base).strip("_")
    return base or "song"

def yes_no(prompt: str, default_no: bool = True) -> bool:
    ans = input(f"{YELLOW}{prompt}{RESET}").strip().lower()
    if ans == "y": return True
    if ans == "n": return False
    return not default_no

def read_text_lines(path: Path) -> List[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in raw.replace("\r\n","\n").replace("\r","\n").split("\n")]
    return [ln for ln in lines if ln != ""]

def time_s() -> float:
    return time.perf_counter()

def audio_duration_seconds(audio_path: Path) -> float:
    if sf is not None:
        try:
            with sf.SoundFile(str(audio_path)) as f:
                return float(len(f) / f.samplerate)
        except Exception:
            pass
    try:
        result = run([
            "ffprobe","-v","error",
            "-show_entries","format=duration",
            "-of","default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ], check=True, capture=True)
        dur = float(result.stdout.strip())
        return dur
    except Exception:
        warn("Unable to read duration; defaulting to 180s ⏱️")
        return 180.0

# ------------- CLI args ------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="🎶 Karaoke Time by Miguel — Perform Along Buddy mode",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--lyrics", required=True, help="Path to lyrics .txt (one line per subtitle line)")
    ap.add_argument("--audio", required=True, help="Path to input song .mp3 or YouTube URL")
    ap.add_argument("--offset", type=float, default=0, help="Global seconds added to each cue start (negative shifts earlier)")
    ap.add_argument("--font-size", type=int, default=140, help="ASS subtitle font size")
    ap.add_argument("--csv", help="Existing timing CSV to reuse. Must have headers line,start")
    ap.add_argument("--ass", help="Existing ASS file to reuse, else regenerated")
    ap.add_argument("--no-prompt", action="store_true", help="Run non-interactive where possible (auto-yes, skip tapping)")
    ap.add_argument("--resolution", default="1280x720", help="Output WxH, example 1280x720")
    ap.add_argument("--fps", type=int, default=30, help="Output FPS")
    ap.add_argument("--bg-color", default="black", help="FFmpeg color background")
    ap.add_argument("--line-hold", type=float, default=2.5, help="Hold duration for final line (sec)")
    ap.add_argument("--model", default="htdemucs_6s", help="Demucs model name")
    ap.add_argument("--device", default=None, help="Demucs device (cpu/cuda). Default auto")
    ap.add_argument("--dry-run", action="store_true", help="Print plan then exit")
    ap.add_argument("--skip-demucs", action="store_true", help="Skip Demucs stem separation and use full mix as-is")
    return ap

# ------------- Tap timing ------------- #

def tap_to_time(lines: List[str], interactive: bool = True) -> List[float]:
    print(f"\n{MAGENTA}🎤 Manual timing mode{RESET}")
    print("Press Enter when each line should APPEAR. Ctrl+C abort.\n")
    if not interactive:
        die("Interactive timing requested but --no-prompt was passed ❌")
    input("🎵 Start playback of the song now, then hit Enter to begin timing...")
    start_zero = time_s()
    starts: List[float] = []
    try:
        for i, line in enumerate(lines, 1):
            print(f"[{i:>3}/{len(lines)}] {line}")
            input("")
            t = time_s() - start_zero
            starts.append(t)
    except KeyboardInterrupt:
        die("Timing interrupted ❌")
    print(f"{GREEN}✅ Timing captured{RESET}\n")
    return starts

# ------------- CSV IO ------------- #

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
        if r.fieldnames is None:
            die("Timing CSV missing headers entirely.")
        lowered = [h.strip().lower() for h in r.fieldnames]
        if lowered != ["line","start"]:
            die(f"Timing CSV must have headers exactly: line,start (found {r.fieldnames})")
        for row in r:
            line_val = row["line"]
            start_val = row["start"]
            try:
                start_f = float(start_val)
            except Exception:
                die(f"Bad start time '{start_val}' in CSV {csv_path}")
            lines.append(line_val)
            starts.append(start_f)
    info(f"📂 Loaded timing CSV → {csv_path}")
    return lines, starts

# ------------- ASS generation ------------- #

ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
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
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

def write_ass(ass_path: Path, w: int, h: int, font_size: int, lines: List[str], starts: List[float], offset: float, default_hold: float):
    n = len(lines)
    ends: List[float] = []
    for i in range(n):
        st = starts[i] + offset
        if i < n - 1:
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
    info(f"🖋️  Wrote ASS → {ass_path}")

# ------------- Demucs + Mixdown ------------- #

STEM_ORDER = ["bass","drums","guitar","other","piano","vocals"]
STEM_MENU = [("Bass","bass"),("Drums","drums"),("Guitar","guitar"),("Other","other"),("Piano","piano"),("Vocals","vocals")]

@dataclass
class StemPlan:
    selected: Dict[str, int]
    all_levels: Dict[str, int]

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
        if sel == "0": break
        if sel in [str(i) for i in range(1,7)]:
            idx = int(sel) - 1; key = STEM_MENU[idx][1]
            chosen.remove(key) if key in chosen else chosen.add(key)
        else: print("Enter 0..6")
    if not chosen:
        levels = {name: 100 for name in STEM_ORDER}
        return StemPlan(selected={}, all_levels=levels)
    names = [label for (label,key) in STEM_MENU if key in chosen]
    yn = input(f"🎛️  You selected: {', '.join(names)} - confirm? [y/N]: ").strip().lower()
    if yn != "y": return interactive_stem_selection(no_prompt=False)
    levels = {name: 100 for name in STEM_ORDER}
    for label, key in STEM_MENU:
        if key in chosen:
            while True:
                val = input(f"{label} volume % (0-100): ").strip()
                if not val: pct = 100; break
                if val.isdigit() and 0 <= int(val) <= 100:
                    pct = int(val); break
                print("0..100 please")
            levels[key] = pct
    print(f"\n{CYAN}Stem levels:{RESET}")
    print("Stem       Vol%")
    print("-------------------")
    for label, key in STEM_MENU: print(f"{label:<10} {levels[key]:>6}")
    yn2 = input("Proceed with Demucs + mix? [y/N]: ").strip().lower()
    if yn2 != "y": return interactive_stem_selection(no_prompt=False)
    selected = {k: levels[k] for k in chosen}
    return StemPlan(selected=selected, all_levels=levels)

def run_demucs_separation(audio_path: Path, model: str, device: Optional[str], out_root: Path) -> Path:
    ensure_dir(out_root)
    base = sanitize_basename(audio_path)
    demucs_dir = out_root / model / base
    if all((demucs_dir / f"{s}.wav").exists() for s in STEM_ORDER):
        info(f"♻️  Reusing stems in {demucs_dir}")
        return demucs_dir
    info("🎵 Running Demucs separation...")
    cmd = ["demucs","--name",model,"--out",str(out_root)]
    if device: cmd += ["--device",device]
    cmd += [str(audio_path)]
    run(cmd, check=True)
    return demucs_dir

def mix_stems_to_file(stem_dir: Path, levels: Dict[str,int], out_mp3: Path):
    inputs=[]; filters=[]; map_segs=[]; idx=0
    for stem in STEM_ORDER:
        wav_path = stem_dir / f"{stem}.wav"
        if not wav_path.exists(): die(f"Missing stem file: {wav_path}")
        inputs += ["-i", str(wav_path)]
        level = levels.get(stem,100)
        if level==100: map_segs.append(f"[{idx}:a]")
        else:
            vol = max(level,0)/100.0
            filters.append(f"[{idx}:a]volume={vol}[a{idx}]")
            map_segs.append(f"[a{idx}]")
        idx+=1
    filter_complex=""
    if filters: filter_complex += ";".join(filters)+";"
    filter_complex += f"{''.join(map_segs)}amix=inputs=6:normalize=0[aout]"
    info("🎚️  Mixing stems into final accompaniment...")
    cmd=["ffmpeg","-y",*inputs,"-filter_complex",filter_complex,"-map","[aout]","-c:a","libmp3lame","-q:a","2",str(out_mp3)]
    run(cmd,check=True)
    info(f"🎧 Mixed audio → {out_mp3}")

# ------------- Video render ------------- #

def render_karaoke_video(audio_path: Path, ass_path: Path, out_mp4: Path, resolution: str, fps: int, bg_color: str):
    dur = audio_duration_seconds(audio_path)
    tmp_video = out_mp4.with_suffix(".tmp_video.mp4")
    subfilter = f"ass={ass_path.as_posix()}"
    info("🎬 Rendering video background + subs...")
    cmd1=["ffmpeg","-y","-f","lavfi","-t",f"{dur:.3f}","-i",f"color=c={bg_color}:s={resolution}:r={fps}","-vf",subfilter,"-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",str(tmp_video)]
    run(cmd1,check=True)
    info("🔗 Muxing vocals/backing into final MP4...")
    cmd2=["ffmpeg","-y","-i",str(tmp_video),"-i",str(audio_path),"-c:v","copy","-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(out_mp4)]
    run(cmd2,check=True)
    try: tmp_video.unlink()
    except Exception: pass
    info(f"📽️  Final video ready → {out_mp4}")

# ------------- misc helpers ------------- #

def print_ytdlp_hint(target_mp3: Path):
    base = target_mp3.name
    print("\n🎧 Audio file not found.")
    print("To fetch an MP3 from YouTube:")
    print(f'yt-dlp -x --audio-format mp3 "<YouTube_URL>" -o "{base}"\n')

# ------------- main() ------------- #

def main():
    ap = build_arg_parser()
    args = ap.parse_args()
    lyrics_path = Path(args.lyrics).expanduser().resolve()
    audio_path  = Path(args.audio).expanduser().resolve()
    if not lyrics_path.exists():
        die(f"Lyrics file not found: {lyrics_path}")
    base = sanitize_basename(audio_path)
    out_dir = Path("output") / base
    ensure_dir(out_dir)

    # --- New additive YouTube filename and custom output logic ---
    if str(args.audio).startswith(("http://","https://")):
        try:
            txt_base = lyrics_path.stem if lyrics_path.exists() else None
            csv_base = Path(args.csv).stem if args.csv else None
            ass_base = Path(args.ass).stem if args.ass else None
            candidates = [b for b in [txt_base, csv_base, ass_base] if b]
            if candidates:
                print(f"\n{YELLOW}Detected possible base names from provided files:{RESET}")
                for i, b in enumerate(candidates, 1):
                    print(f"  {i}. {b}")
                print("  0. Keep default (YouTube ID or current name)")
                sel = input("> ").strip()
                chosen = None
                if sel.isdigit() and int(sel) in range(1,len(candidates)+1):
                    chosen = candidates[int(sel)-1]
                if chosen:
                    print(f"\n{CYAN}Choosing '{chosen}' as output base will rename the mp3/mp4 and affect:{RESET}")
                    print("  - CSV/ASS naming defaults will also use this base.")
                    print("  - Output folder will change accordingly.")
                    confirm = input(f"Confirm rename outputs to '{chosen}.mp3/.mp4'? [y/N]: ").strip().lower()
                    if confirm=="y":
                        renamed_mp3 = Path('songs') / f"{chosen}.mp3"
                        try:
                            if audio_path.exists() and audio_path!=renamed_mp3:
                                audio_path.rename(renamed_mp3)
                                info(f"Renamed {audio_path.name} → {renamed_mp3.name}")
                        except Exception as e:
                            warn(f"Rename skipped: {e}")
                        audio_path = renamed_mp3
                        base = sanitize_basename(audio_path)
                        out_dir = Path("output") / base
                        ensure_dir(out_dir)
            user_custom_files=[]
            if args.csv or args.ass or args.lyrics:
                print(f"\n{MAGENTA}Would you like to set custom filenames for any outputs?{RESET}")
                print("  This can override where CSV or ASS are written or reused.")
                resp=input("  Enter 'y' to continue or any key to skip: ").strip().lower()
                if resp=="y":
                    if args.csv:
                        new_csv=input(f"Enter custom CSV filename (blank = keep '{Path(args.csv).name}'): ").strip()
                        if new_csv:
                            csv_path=(out_dir/new_csv).with_suffix('.csv'); user_custom_files.append(str(csv_path))
                    elif not args.csv:
                        new_csv=input("Enter new CSV filename (blank = skip): ").strip()
                        if new_csv:
                            csv_path=(out_dir/new_csv).with_suffix('.csv'); user_custom_files.append(str(csv_path))
                    if args.ass:
                        new_ass=input(f"Enter custom ASS filename (blank = keep '{Path(args.ass).name}'): ").strip()
                        if new_ass:
                            ass_path=(out_dir/new_ass).with_suffix('.ass'); user_custom_files.append(str(ass_path))
                    elif not args.ass:
                        new_ass=input("Enter new ASS filename (blank = skip): ").strip()
                        if new_ass:
                            ass_path=(out_dir/new_ass).with_suffix('.ass'); user_custom_files.append(str(ass_path))
                    if user_custom_files:
                        print(f"\n{GREEN}✅ Confirmed custom file targets:{RESET}")
                        for f in user_custom_files: print(f"  {f}")
                        input(f"{YELLOW}Press Enter to confirm and continue...{RESET}")
        except Exception as e:
            warn(f"Filename customization skipped: {e}")

    # handle YouTube URL case
    if str(args.audio).startswith(("http://","https://")):
        url = args.audio.strip()
        info(f"Detected remote audio URL: {url}")
        ensure_dir(Path("songs"))
        base_from_url = sanitize_basename(Path(url.split("v=")[-1]))
        if not base_from_url:
            base_from_url = "youtube_song"
        download_mp3 = Path("songs") / f"{base_from_url}.mp3"
        if not download_mp3.exists():
            info(f"Downloading audio with yt-dlp → {download_mp3}")
            try:
                run(["yt-dlp","-x","--audio-format","mp3","-o",str(download_mp3),url],check=True)
            except subprocess.CalledProcessError as e:
                die(f"yt-dlp failed: {e}")
        else:
            info(f"Reusing cached MP3 {download_mp3}")
        audio_path = download_mp3
        base = sanitize_basename(audio_path)
        out_dir = Path("output") / base
        ensure_dir(out_dir)

    # derive paths
    csv_path  = Path(args.csv).expanduser().resolve() if args.csv else (out_dir / f"{base}_timing.csv")
    ass_path  = Path(args.ass).expanduser().resolve() if args.ass else (out_dir / f"{base}_subtitles.ass")
    buddy_mp3 = out_dir / f"{base}_buddy_mix.mp3"
    instr_mp3 = out_dir / f"{base}_instrumental.mp3"
    final_mp4 = out_dir / (f"{base}_karaoke.mp4")

    if not audio_path.exists():
        print_ytdlp_hint(audio_path)
        die(f"Audio mp3 not found: {audio_path}")

    ensure_bins(require_demucs=not args.skip_demucs)
    if args.skip_demucs:
        info("🎵 --skip-demucs set → full mix will be used. No stem separation.")
        stem_plan=StemPlan(selected={},all_levels={k:100 for k in STEM_ORDER})
        any_change=False; target_audio_for_mix=audio_path
    else:
        stem_plan=interactive_stem_selection(no_prompt=args.no_prompt)
        any_change=any(v!=100 for v in stem_plan.all_levels.values())
        target_audio_for_mix=buddy_mp3 if any_change else instr_mp3

    print(f"\n{BLUE}===== PLAN SUMMARY ====={RESET}")
    print(f"Lyrics file     : {lyrics_path}")
    print(f"Audio input     : {audio_path}")
    print(f"Output dir      : {out_dir}")
    print(f"Timing CSV path : {csv_path}")
    print(f"ASS path        : {ass_path}")
    print(f"Final video     : {final_mp4}")
    print("\nStem levels (percent):")
    for label,key in STEM_MENU: print(f"  {label:<10} {stem_plan.all_levels[key]:>3}%")
    print(f"\n🎵 Mix target MP3 : {target_audio_for_mix.name}")
    print("========================\n")

    if not args.no_prompt and not args.skip_demucs:
        proceed=yes_no("Proceed with Demucs separation and audio mixing? [y/N]: ",default_no=True)
        if not proceed: die("Cancelled before Demucs ❌")

    if args.dry_run:
        print("[dry-run] exiting before processing."); return

    if not args.skip_demucs:
        demucs_out_root=out_dir/"demucs_stems"
        demucs_dir=run_demucs_separation(audio_path,args.model,args.device,demucs_out_root)
        levels=dict(stem_plan.all_levels)
        if not any_change: levels["vocals"]=0
        info("🎛️  Mixing stems into mp3...")
        target_audio_for_mix=buddy_mp3 if any_change else instr_mp3
        mix_stems_to_file(demucs_dir,levels,target_audio_for_mix)
        mixed_audio_path=target_audio_for_mix
    else: mixed_audio_path=audio_path

    if csv_path.exists():
        info(f"🕒 Reusing timing CSV: {csv_path}")
        lines,starts=read_timing_csv(csv_path)
    else:
        lines=read_text_lines(lyrics_path)
        if not lines: die("Lyrics file has no usable lines after trimming.")
        if args.no_prompt: die("No CSV provided. --no-prompt prevents interactive tapping.")
        starts=tap_to_time(lines,interactive=True)
        write_timing_csv(csv_path,lines,starts)
        info(f"💾 Saved timing CSV: {csv_path}")

    m=re.match(r"^(\d+)x(\d+)$",args.resolution.strip().lower())
    if not m: die(f"Bad --resolution: {args.resolution}")
    w,h=int(m.group(1)),int(m.group(2))
    write_ass(ass_path,w,h,args.font_size,lines,starts,args.offset,args.line_hold)
    info("🎬 Rendering final MP4 with subtitles...")
    render_karaoke_video(mixed_audio_path,ass_path,final_mp4,args.resolution,args.fps,args.bg_color)
    info(f"{GREEN}✅ Done. Output: {final_mp4}{RESET}")
    print(f"{MAGENTA}🎉 Enjoy your karaoke video 🎶{RESET}")
    choice=input("\nOpen output folder or video? [f=folder / v=video / n=none]: ").strip().lower()
    if choice=="f": subprocess.run(["open",str(final_mp4.parent)])
    elif choice=="v": subprocess.run(["open",str(final_mp4)])

# ------------- guard ------------- #

if __name__=="__main__":
    try: main()
    except subprocess.CalledProcessError as e:
        print("\n[error] A subprocess failed.",file=sys.stderr)
        if e.stdout: print(e.stdout,file=sys.stderr)
        if e.stderr: print(e.stderr,file=sys.stderr)
        sys.exit(e.returncode)
    except Exception as ex:
        print(f"\n[error] {ex}",file=sys.stderr)
        sys.exit(1)

# end of karaoke_time_by_miguel.py
