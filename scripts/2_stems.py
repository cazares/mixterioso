
#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

from mix_utils import log, ensure_pipeline_dirs

def run_demucs(mp3: Path, model: str):
    out_dir = Path("separated") / model
    if out_dir.exists():
        log("STEMS", f"Removing existing stems directory: {out_dir}")
        subprocess.run(["rm", "-rf", str(out_dir)], check=True)

    cmd = [
        "demucs",
        "-n", model,
        str(mp3),
    ]
    log("DEMUX", f"Running Demucs model={model} (FORCE overwrite)")
    subprocess.run(cmd, check=True)


def main():
    ensure_pipeline_dirs()

    ap = argparse.ArgumentParser()
    ap.add_argument("--mp3", required=True)
    ap.add_argument("--model", default="htdemucs")
    ap.add_argument("--no-ui", action="store_true")
    args = ap.parse_args()

    mp3 = Path(args.mp3)
    if not mp3.exists():
        raise FileNotFoundError(mp3)

    run_demucs(mp3, args.model)


if __name__ == "__main__":
    main()
# end of 2_stems.py
