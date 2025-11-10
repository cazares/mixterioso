#!/usr/bin/env python3
import argparse, json, subprocess, time
from pathlib import Path

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

def log(prefix, msg, color=RESET):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{prefix}] {msg}{RESET}")

def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return float(out)
    except Exception:
        return 0.0

def slice_preview(audio_path: Path, start_s: float, end_s: float, out_path: Path):
    if out_path.exists():
        out_path.unlink()
    dur = max(0, end_s - start_s)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start_s:.3f}", "-t", f"{dur:.3f}",
        "-i", str(audio_path),
        "-c", "copy", str(out_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def run_preview(video_path: Path):
    cmd = ["ffplay", "-autoexit", str(video_path)]
    subprocess.run(cmd)

def main():
    p = argparse.ArgumentParser(description="Offset calibration lab for short karaoke previews.")
    p.add_argument("--slug", required=True)
    p.add_argument("--start", type=float, default=30.0)
    p.add_argument("--end", type=float, default=45.0)
    p.add_argument("--initial-offset", type=float, default=-1.5)
    args = p.parse_args()

    base = Path(__file__).resolve().parent.parent
    mp3 = base / "mp3s" / f"{args.slug}.mp3"
    meta = base / "meta" / f"{args.slug}.json"
    previews = base / "output" / "previews"
    previews.mkdir(exist_ok=True)

    dur = ffprobe_duration(mp3)
    start, end = min(args.start, dur), min(args.end, dur)
    offset = args.initial_offset
    log("CAL", f"Audio={mp3}, window={start:.1f}s–{end:.1f}s, initial offset={offset:+.3f}", CYAN)

    while True:
        log("CAL", f"Generating preview offset={offset:+.3f}s", GREEN)
        slice_path = previews / f"{args.slug}_{start:.0f}-{end:.0f}.mp3"
        slice_preview(mp3, start, end, slice_path)

        cmd = [
            "python3", str(base / "scripts" / "5_mp4.py"),
            "--slug", args.slug,
            "--profile", "lyrics",
            "--offset", str(offset),
            "--slice-start", str(start),
            "--slice-end", str(end),
            "--preview"
        ]
        subprocess.run(cmd)

        run_preview(previews / f"{args.slug}_preview.mp4")

        try:
            new = input(f"\nNew offset (ENTER to accept {offset:+.3f}s): ").strip()
            if not new:
                break
            offset = float(new)
        except KeyboardInterrupt:
            break
        except Exception:
            log("CAL", "Invalid input, try again", RED)

    meta.parent.mkdir(exist_ok=True)
    data = {"offset": offset}
    meta.write_text(json.dumps(data, indent=2))
    log("CAL", f"Saved offset={offset:+.3f}s → {meta}", GREEN)

if __name__ == "__main__":
    main()

# end of 4_calibrate.py
