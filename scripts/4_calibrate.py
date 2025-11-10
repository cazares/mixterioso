#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def log(prefix: str, msg: str, color: str = RESET) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{prefix}] {msg}{RESET}")


def load_offset_from_meta(meta_path: Path) -> float:
    if not meta_path.exists():
        return 0.0
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    value = data.get("offset")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def save_offset_to_meta(meta_path: Path, offset: float) -> None:
    data = {}
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["offset"] = float(offset)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log("CAL", f"Saved offset={offset:+.3f}s -> {meta_path}", GREEN)


def run_preview_ffplay(video_path: Path, start: float, end: float) -> None:
    if not video_path.exists():
        log("CAL", f"Preview MP4 not found: {video_path}", RED)
        return
    duration = max(0.0, end - start)
    cmd = [
        "ffplay",
        "-autoexit",
        "-ss",
        f"{max(0.0, start):.3f}",
    ]
    if duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd.append(str(video_path))
    log("CAL", " ".join(cmd), CYAN)
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Offset calibration lab for short karaoke previews.",
    )
    p.add_argument("--slug", required=True, help="Song slug, e.g. californication")
    p.add_argument(
        "--profile",
        type=str,
        default="lyrics",
        help="Profile to use for previews (default: lyrics).",
    )
    p.add_argument(
        "--start",
        type=float,
        default=30.0,
        help="Preview window start time in seconds (default 30).",
    )
    p.add_argument(
        "--end",
        type=float,
        default=45.0,
        help="Preview window end time in seconds (default 45).",
    )
    p.add_argument(
        "--initial-offset",
        type=float,
        default=None,
        help="Initial offset in seconds. If omitted, uses meta offset or env.",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base = Path(__file__).resolve().parent.parent

    slug = args.slug
    profile = args.profile

    mp3 = base / "mp3s" / f"{slug}.mp3"
    meta = base / "meta" / f"{slug}.json"
    previews_dir = base / "output" / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    preview_mp4 = previews_dir / f"{slug}_{profile}_preview.mp4"

    # Determine starting offset: CLI > meta > env > default -1.5
    if args.initial_offset is not None:
        offset = float(args.initial_offset)
    else:
        offset = load_offset_from_meta(meta)
        if offset == 0.0:
            env_val = os.getenv("KARAOKE_OFFSET_SECS")
            if env_val:
                try:
                    offset = float(env_val)
                except ValueError:
                    offset = -1.5
            else:
                offset = -1.5

    start = max(0.0, float(args.start))
    end = max(start, float(args.end))

    log(
        "CAL",
        f"Audio={mp3}, window={start:.1f}s–{end:.1f}s, initial offset={offset:+.3f}",
        CYAN,
    )

    while True:
        env = os.environ.copy()
        env["KARAOKE_OFFSET_SECS"] = f"{offset:+.3f}"

        cmd = [
            sys.executable,
            str(base / "scripts" / "4_mp4.py"),
            "--slug",
            slug,
            "--profile",
            profile,
            "--output-mp4",
            str(preview_mp4),
        ]
        log("CAL", f"Generating preview offset={offset:+.3f}s", GREEN)
        try:
            subprocess.run(cmd, check=True, env=env)
        except subprocess.CalledProcessError as e:
            log("CAL", f"Preview render failed (exit {e.returncode}).", RED)
            return

        run_preview_ffplay(preview_mp4, start, end)

        try:
            new = input(
                f"New offset in seconds (ENTER to accept {offset:+.3f} and save, Ctrl+C to abort): "
            ).strip()
        except KeyboardInterrupt:
            print()
            log("CAL", "Interrupted; not changing stored offset.", YELLOW)
            return

        if not new:
            break

        try:
            offset = float(new)
        except ValueError:
            log("CAL", "Invalid offset, try again.", RED)

    save_offset_to_meta(meta, offset)


if __name__ == "__main__":
    main()

# end of 4_calibrate.py
