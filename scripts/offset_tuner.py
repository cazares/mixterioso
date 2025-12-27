#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path

STEP = 0.25
PREVIEW_LEN = 10.0
PRE_ROLL = 1.0


def tune_offset(*, slug: str, base_offset: float, mixes_dir: Path, timings_dir: Path, renderer_path: Path) -> float:
    offset = base_offset

    audio = mixes_dir / f"{slug}.wav"
    if not audio.exists():
        audio = mixes_dir / f"{slug}.mp3"

    timings = timings_dir / f"{slug}.csv"

    if not audio.exists():
        raise RuntimeError(f"Missing audio file for preview: {audio}")
    if not timings.exists():
        raise RuntimeError(f"Missing timings CSV for preview: {timings}")

    while True:
        print("\n────────────────────────────────────────")
        print(f"Current offset: {offset:+.2f}s\n")
        print("[1] Earlier (-0.25s)")
        print("[2] Later   (+0.25s)")
        print("[3] Play preview (10s)")
        print("[4] Lock offset and continue")
        print("[5] Abort")

        choice = input("> ").strip()

        if choice == "1":
            offset -= STEP
        elif choice == "2":
            offset += STEP
        elif choice == "3":
            with tempfile.TemporaryDirectory() as tmp:
                preview_slug = f"{slug}_preview"
                cmd = [
                    sys.executable,
                    str(renderer_path),
                    "--slug", slug,
                    "--offset", str(offset),
                    "--preview",
                ]
                subprocess.run(cmd, check=True)
        elif choice == "4":
            return offset
        elif choice == "5":
            sys.exit(1)
        else:
            print("Invalid choice.")
