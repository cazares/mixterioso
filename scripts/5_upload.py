#!/usr/bin/env python3
# scripts/5_upload.py
#
# Unified YouTube upload (private by default).
# Used by both 0_master and 4_mp4 UI.
#
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"
UPLOAD_LOG = BASE_DIR / "uploaded"

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"


def log(section, msg, color=CYAN):
    print(f"{color}[{section}]{RESET} {msg}")


def load_meta(slug: str) -> dict:
    mp = META_DIR / f"{slug}.json"
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_upload_receipt(slug: str, profile: str, offset: float, video_id: str, title: str):
    UPLOAD_LOG.mkdir(parents=True, exist_ok=True)
    receipt = {
        "slug": slug,
        "profile": profile,
        "offset": offset,
        "video_id": video_id,
        "title": title,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    out = UPLOAD_LOG / f"{slug}_{profile}_offset_{offset:+.3f}.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return out


def run_uploader(file_path: str, title: str, privacy: str):
    """
    Call yt-uploader-python as a subprocess:
      --file, --title, --privacy
    Returns (video_id, url)
    """
    cmd = [
        sys.executable,
        "-m",
        "yt_uploader_python",
        "--file",
        file_path,
        "--title",
        title,
        "--privacy",
        privacy,
        "--thumb",
        f"{file_path}.thumb.png",
    ]
    log("upload", " ".join(cmd), BLUE)

    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        raise SystemExit(
            f"Upload failed with exit {cp.returncode}:\n{cp.stderr.strip()}"
        )

    # yt-uploader prints JSON
    out = cp.stdout.strip()
    try:
        data = json.loads(out)
    except Exception:
        print(out)
        raise SystemExit("Uploader returned non-JSON output")

    if not data.get("ok"):
        raise SystemExit(f"Uploader error: {data}")

    return data["video_id"], data["watch_url"]


def parse_args():
    p = argparse.ArgumentParser(description="Upload MP4 to YouTube privately")
    p.add_argument("--file", required=True, help="MP4 path")
    p.add_argument("--slug", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--offset", type=float, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--privacy", default="private")
    return p.parse_args()


def main():
    args = parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"File not found: {file_path}")

    # Ensure privacy = private
    privacy = args.privacy.lower().strip() or "private"

    log("upload", f"Uploading {file_path.name} as '{args.title}' [{privacy}]", CYAN)

    video_id, url = run_uploader(str(file_path), args.title, privacy)
    log("upload", f"Upload successful. Video ID: {video_id}", GREEN)
    log("upload", f"Watch: {url}", GREEN)

    receipt = write_upload_receipt(
        args.slug, args.profile, args.offset, video_id, args.title
    )
    log("upload", f"Saved upload receipt to {receipt}", GREEN)

    print(json.dumps({"ok": True, "video_id": video_id, "watch_url": url}, indent=2))


if __name__ == "__main__":
    main()

# end of 5_upload.py
