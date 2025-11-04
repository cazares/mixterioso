#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def infer_slug_from_mp3(mp3_path: Path) -> str:
    return slugify(mp3_path.stem)


def run_pre_tracking(query: str) -> str:
    cmd = [sys.executable, str(SCRIPTS_DIR / "pre_tracking.py"), query]
    log("PRE", f"Running pre_tracking: {' '.join(cmd)}", CYAN)
    subprocess.run(cmd, check=True)
    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("pre_tracking.py did not produce any mp3s.")
    slug = infer_slug_from_mp3(mp3s[-1])
    return slug


def run_demucs_background(mp3_path: Path, model: str) -> subprocess.Popen:
    cmd = ["demucs", "-n", model, str(mp3_path)]
    log("DEMUX", f"Starting Demucs in background: {' '.join(cmd)}", YELLOW)
    return subprocess.Popen(cmd)


def run_mix_ui(slug: str, txt_path: Path, mp3_path: Path, profile: str) -> None:
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
    subprocess.run(cmd, check=True)


def run_timing_editor(slug: str, txt_path: Path, audio_path: Path) -> None:
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
    log("TIME", f"Launching timing editor: {' '.join(cmd)}", CYAN)
    subprocess.run(cmd, check=True)


def run_render(slug: str, mp3_path: Path, profile: str, model: str) -> Path:
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
    log("RENDER", f"Rendering mix: {' '.join(cmd)}", CYAN)
    subprocess.run(cmd, check=True)
    return output


def run_ffmpeg_mp4(slug: str, audio_path: Path, profile: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=size=1920x1080:duration=5:rate=30:color=black",
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(out_mp4),
    ]
    log("MP4", f"Rendering placeholder MP4 (no subs yet): {' '.join(cmd)}", CYAN)
    subprocess.run(cmd, check=True)
    return out_mp4


def parse_args(argv):
    p = argparse.ArgumentParser(description="Orchestrate full pipeline to MP4.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", type=str, help="Song search query for pre_tracking.")
    group.add_argument("--mp3", type=str, help="Existing mp3 path.")
    p.add_argument("--txt", type=str, help="Existing lyrics txt path.")
    p.add_argument("--profile", type=str, default="karaoke",
                   choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"])
    p.add_argument("--model", type=str, default="htdemucs_6s")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    if args.query:
        slug = run_pre_tracking(args.query)
        mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        mp3_path = mp3s[-1]
        txts = sorted(TXT_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        if not txts:
            raise SystemExit("No txts found after pre_tracking.")
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

    log("MP4GEN", f"Slug={slug}", GREEN)

    # 1) Start Demucs in background
    demucs_proc = run_demucs_background(mp3_path, args.model)

    # 2) Mix UI (foreground)
    run_mix_ui(slug, txt_path, mp3_path, args.profile)

    # 3) Timing editor (foreground), against original mp3
    run_timing_editor(slug, txt_path, mp3_path)

    # 4) Wait for Demucs
    log("DEMUX", "Waiting for Demucs to finish...", YELLOW)
    demucs_proc.wait()
    log("DEMUX", "Demucs finished.", GREEN)

    # 5) Render final mix from stems + mix config
    mix_audio = run_render(slug, mp3_path, args.profile, args.model)

    # 6) Simple MP4 (placeholder, audio + black frame)
    out_mp4 = run_ffmpeg_mp4(slug, mix_audio, args.profile)

    log("DONE", f"MP4 written to {out_mp4}", GREEN)


if __name__ == "__main__":
    main()
