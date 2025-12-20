
#!/usr/bin/env python3
import subprocess, sys, argparse
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mix_utils import ensure_pipeline_dirs

PY = sys.executable

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True, help="YouTube search query")
    args = parser.parse_args()

    ensure_pipeline_dirs()

    def step(script: str, *extra):
        p = SCRIPTS_DIR / script
        if not p.exists():
            raise FileNotFoundError(p)
        return [
            PY,
            str(p),
            "--query", args.query,
            *map(str, extra),
        ]

    # Step 1: fetch (blocking)
    subprocess.run(step("1_fetch.py"), check=True)

    # Step 2: demucs (background)
    demucs = subprocess.Popen(step("2_stems.py"))

    # Step 3: timing only if no LRC
    timings = list((REPO_ROOT / "timings").glob("*.lrc"))
    ran_review = False
    if not timings:
        subprocess.run(step("3_timing.py", "--review"), check=True)
        ran_review = True

    demucs.wait()

    # Step 4: mp4 render
    subprocess.run(
        step("4_mp4.py", "--manual" if ran_review else "--lrc"),
        check=True
    )

    # Step 5: upload (background)
    subprocess.Popen(step("5_upload.py"))

if __name__ == "__main__":
    run()
# end of 0_main.py
