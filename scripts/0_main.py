
#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

# ─────────────────────────────────────────────
# PATH SETUP
# ─────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mix_utils import log, ensure_pipeline_dirs

PY = sys.executable


def run():
    ensure_pipeline_dirs()

    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    args = ap.parse_args()

    # ─────────────────────────────────────────────
    # Step 1: Fetch (blocking, user interaction OK)
    # ─────────────────────────────────────────────
    subprocess.run(
        [PY, SCRIPTS_DIR / "1_fetch.py", "--query", args.query],
        check=True,
    )

    # Read slug written by fetch step
    meta_dir = REPO_ROOT / "meta"
    meta_files = sorted(meta_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not meta_files:
        raise RuntimeError("No meta JSON produced by fetch step")
    slug = meta_files[-1].stem

    mp3 = REPO_ROOT / "mp3s" / f"{slug}.mp3"
    if not mp3.exists():
        raise FileNotFoundError(mp3)

    # ─────────────────────────────────────────────
    # Step 2: DEMUCS — BACKGROUND ONLY
    # ─────────────────────────────────────────────
    log("PIPE", "Starting Demucs in background")
    demucs_proc = subprocess.Popen(
        [
            PY,
            SCRIPTS_DIR / "2_stems.py",
            "--mp3",
            str(mp3),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # ─────────────────────────────────────────────
    # Step 3: Timing (FOREGROUND, owns stdin)
    # ─────────────────────────────────────────────
    subprocess.run(
        [
            PY,
            SCRIPTS_DIR / "3_timing.py",
            "--slug",
            slug,
            "--auto",
        ],
        check=True,
    )

    # ─────────────────────────────────────────────
    # Step 4: Wait for Demucs ONLY now
    # ─────────────────────────────────────────────
    log("PIPE", "Waiting for Demucs to finish")
    demucs_proc.wait()

    # ─────────────────────────────────────────────
    # Step 5: Video (non-interactive)
    # ─────────────────────────────────────────────
    subprocess.run(
        [
            PY,
            SCRIPTS_DIR / "4_mp4.py",
            "--slug",
            slug,
        ],
        check=True,
    )

    # ─────────────────────────────────────────────
    # Step 6: Upload (background-safe prompt)
    # ─────────────────────────────────────────────
    subprocess.run(
        [
            PY,
            SCRIPTS_DIR / "5_upload.py",
            "--slug",
            slug,
        ],
        check=True,
    )


if __name__ == "__main__":
    run()
# end of 0_main.py
