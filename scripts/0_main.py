
#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

import sys
import termios
import tty

def restore_terminal():
    fd = sys.stdin.fileno()
    attrs = termios.tcgetattr(fd)
    attrs[3] |= termios.ICANON | termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PY = sys.executable

def run():
    if len(sys.argv) < 3 or sys.argv[1] != "--query":
        print("Usage: 0_main.py --query <search>")
        sys.exit(1)

    query = sys.argv[2]

    print(f"[PLAN] Query: {query}")
    print("[PLAN] Steps:")
    print("  1) Fetch lyrics + audio")
    print("  2) Split stems (Demucs) [background if needed]")
    print("  3) Resolve / review timings [foreground]")
    print("  4) (Video + upload not invoked here)")

    # Step 1: fetch txt + mp3 + meta
    subprocess.run(
        [PY, SCRIPTS_DIR / "1_fetch.py", "--query", query],
        check=True,
    )

    # Infer slug from meta (last written json)
    meta_dir = REPO_ROOT / "meta"
    metas = sorted(meta_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not metas:
        raise RuntimeError("No meta json found after fetch")
    slug = metas[-1].stem

    print(f"[SLUG] Using slug '{slug}'")

    mp3 = REPO_ROOT / "mp3s" / f"{slug}.mp3"
    if not mp3.exists():
        raise RuntimeError(f"MP3 not found: {mp3}")

    separated_dir = REPO_ROOT / "separated" / slug
    demucs_needed = not separated_dir.exists()

    demucs_proc = None
    if demucs_needed:
        print("[STEP2] Starting Demucs in background")
        demucs_proc = subprocess.Popen(
            [
                PY,
                SCRIPTS_DIR / "2_stems.py",
                "--mp3", str(mp3),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        print("[STEP2] Demucs output exists, skipping")

    # Step 3: timing resolution + review (foreground)
    print("[STEP3] Resolving timings / review")
    subprocess.run(
        ["bash", "-lc", f"{PY} scripts/3_timing.py --slug {slug}"],
        check=True,
    )

    # Ensure Demucs finished before exiting
    if demucs_proc:
        print("[WAIT] Waiting for Demucs to finish")
        demucs_proc.wait()
        print("[STEP2] Demucs completed")

    print("[DONE] Inputs, stems, and timings ready")

if __name__ == "__main__":
    run()
# end of 0_main.py
