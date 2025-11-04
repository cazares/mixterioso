#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
BLUE = "\033[34m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
META_DIR = BASE_DIR / "meta"


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def infer_slug_from_mp3(mp3_path: Path) -> str:
    return slugify(mp3_path.stem)


def run_pre_tracking(query: str) -> tuple[str, float]:
    cmd = [sys.executable, str(SCRIPTS_DIR / "pre_tracking.py"), query]
    log("PRE", f"Running pre_tracking: {' '.join(cmd)}", MAGENTA)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("pre_tracking.py did not produce any mp3 files")
    slug = infer_slug_from_mp3(mp3s[-1])
    return slug, t1 - t0


def run_demucs_background(mp3_path: Path, model: str) -> tuple[subprocess.Popen, float]:
    cmd = ["demucs", "-n", model, str(mp3_path)]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    demucs_log_path = LOGS_DIR / f"demucs_{mp3_path.stem}.log"
    log("DEMUX", f"Starting Demucs in background: {' '.join(cmd)}", YELLOW)
    log("DEMUX", f"Demucs output → {demucs_log_path}", YELLOW)
    t0 = time.perf_counter()

    log_file = demucs_log_path.open("w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
    proc._demucs_log = log_file  # keep handle around

    return proc, t0


def run_mix_ui(slug: str, txt_path: Path, mp3_path: Path, profile: str) -> float:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "tracking.py"),
        "--txt",
        str(txt_path),
        "--mp3",
        str(mp3_path),
        "--profile",
        profile,
        "--mix-ui-only",
    ]
    log("MIX", f"Launching mix UI: {' '.join(cmd)}", CYAN)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    return t1 - t0


def run_timing_editor(slug: str, txt_path: Path, audio_path: Path) -> float:
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "timing_editor.py"),
        "--txt",
        str(txt_path),
        "--audio",
        str(audio_path),
        "--timings",
        str(timing_path),
    ]
    log("TIME", f"Launching timing editor: {' '.join(cmd)}", GREEN)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    return t1 - t0


def run_render(slug: str, mp3_path: Path, profile: str, model: str) -> tuple[Path, float]:
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    mix_cfg = MIXES_DIR / f"{slug}.json"
    output = MIXES_DIR / f"{slug}_{profile}.wav"
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "tracking.py"),
        "--mp3",
        str(mp3_path),
        "--profile",
        profile,
        "--mix-config",
        str(mix_cfg),
        "--render-only",
        "--reuse-stems",
        "--model",
        model,
        "--output",
        str(output),
    ]
    log("RENDER", f"Rendering mix: {' '.join(cmd)}", BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    return output, t1 - t0


def run_ffmpeg_mp4(slug: str, audio_path: Path, profile: str, title: str | None, artist: str | None) -> tuple[Path, float]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}.mp4"

    if title and artist:
        raw_text = f"{title}\n\nby\n\n{artist}"
    elif title:
        raw_text = title
    else:
        raw_text = slug.replace("_", " ")

    # escape for drawtext
    drawtext_text = (
        raw_text
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("\n", r"\n")
    )

    filter_complex = (
        f"[0:v]drawtext=text='{drawtext_text}':"
        "fontcolor=white:fontsize=64:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        "enable='lte(t,3)'[v]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=size=1920x1080:rate=30:color=black",
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(out_mp4),
    ]
    log("MP4", f"Rendering MP4 with title card: {' '.join(cmd)}", CYAN)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()

    log("MP4", f'Title card "{raw_text.replace(chr(10), " / ")}" inserted at 0:00 (first ~3s).', GREEN)
    return out_mp4, t1 - t0


def parse_args(argv):
    p = argparse.ArgumentParser(description="Orchestrate full pipeline to MP4")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", type=str, help="Song search query for pre_tracking")
    group.add_argument("--mp3", type=str, help="Existing mp3 path")
    p.add_argument("--txt", type=str, help="Existing lyrics txt path")
    p.add_argument("--profile", type=str, default="karaoke",
                   choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"])
    p.add_argument("--model", type=str, default="htdemucs_6s")
    return p.parse_args(argv)


def load_meta_for_slug(slug: str) -> tuple[str | None, str | None]:
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        log("META", f"No metadata for slug={slug}; title card will use slug only.", YELLOW)
        return None, None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        artist = data.get("artist")
        title = data.get("title")
        log("META", f'Loaded metadata: "{title}" by "{artist}"', GREEN)
        return title, artist
    except Exception as e:
        log("META", f"Failed to load metadata from {meta_path}: {e}", YELLOW)
        return None, None


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    t_total_start = time.perf_counter()

    t_pre = 0.0
    t_demux = 0.0
    t_mix_ui = 0.0
    t_timing = 0.0
    t_render = 0.0
    t_mp4 = 0.0

    if args.query:
        slug, t_pre = run_pre_tracking(args.query)
        mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        mp3_path = mp3s[-1]
        txts = sorted(TXT_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        if not txts:
            raise SystemExit("No txts found after pre_tracking")
        txt_path = txts[-1]
        slug = infer_slug_from_mp3(mp3_path)
    else:
        mp3_path = Path(args.mp3).resolve()
        if not mp3_path.exists():
            raise SystemExit(f"mp3 not found: {mp3_path}")
        if args.txt:
            txt_path = Path(args.txt).resolve()
        else:
            txt_candidate = TXT_DIR / (mp3_path.stem + ".txt")
            if not txt_candidate.exists():
                raise SystemExit(f"txt not found: {txt_candidate}")
            txt_path = txt_candidate
        slug = infer_slug_from_mp3(mp3_path)

    # read title/artist for title card if available
    title, artist = load_meta_for_slug(slug)

    log("MP4GEN", f"Slug={slug}", GREEN)

    # 1) Start Demucs in background (silent into log file)
    demucs_proc, t_demux_start = run_demucs_background(mp3_path, args.model)

    # 2) Mix UI (foreground)
    t_mix_ui = run_mix_ui(slug, txt_path, mp3_path, args.profile)

    # 3) Timing editor (foreground), against original mp3
    t_timing = run_timing_editor(slug, txt_path, mp3_path)

    # 4) Wait for Demucs
    log("DEMUX", "Waiting for Demucs to finish", YELLOW)
    demucs_proc.wait()
    t_demux_end = time.perf_counter()
    t_demux = t_demux_end - t_demux_start
    log("DEMUX", "Demucs finished", GREEN)

    # 5) Render final mix from stems + mix config
    mix_audio, t_render = run_render(slug, mp3_path, args.profile, args.model)

    # 6) MP4 with title card
    out_mp4, t_mp4 = run_ffmpeg_mp4(slug, mix_audio, args.profile, title, artist)

    t_total_end = time.perf_counter()
    t_total = t_total_end - t_total_start

    print()
    print(f"{BOLD}{BLUE}========== PIPELINE SUMMARY ({slug}, profile={args.profile}) =========={RESET}")
    if args.query:
        print(f"{MAGENTA}pre_tracking.py:   {t_pre:6.2f} s{RESET}")
    else:
        print(f"{MAGENTA}pre_tracking.py:   {t_pre:6.2f} s (skipped, using existing assets){RESET}")
    print(f"{YELLOW}Demucs (bg):       {t_demux:6.2f} s{RESET}")
    print(f"{CYAN}Mix UI:            {t_mix_ui:6.2f} s{RESET}")
    print(f"{GREEN}Timing editor:     {t_timing:6.2f} s{RESET}")
    print(f"{BLUE}Audio render:      {t_render:6.2f} s{RESET}")
    print(f"{CYAN}MP4 render:        {t_mp4:6.2f} s{RESET}")
    print(f"{BOLD}{GREEN}Total mp4_gen:     {t_total:6.2f} s{RESET}")
    print(f"{BOLD}{GREEN}Output MP4:        {out_mp4}{RESET}")
    print(f"{BOLD}{BLUE}========================================================={RESET}")

    # end of main


if __name__ == "__main__":
    main()

# end of mp4_gen.py
