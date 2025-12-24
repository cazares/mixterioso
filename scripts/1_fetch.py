#!/usr/bin/env python3
"""
1_fetch.py — Fetch lyrics + audio + meta (production-grade)

Responsibilities:
- Fetch lyrics.txt via fetch_lyrics.py (best-effort, never fatal)
- Fetch music.mp3 via fetch_audio.py (required)
- Write meta/<slug>.json
- No timing, no stems, no video
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
import time

# ─────────────────────────────────────────────
# Paths / bootstrap
# ─────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
ROOT = SCRIPTS_DIR.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import log, CYAN, GREEN, YELLOW, RED, slugify, PATHS

TXT_DIR   = PATHS["txt"]
MP3_DIR   = PATHS["mp3"]
META_DIR  = PATHS["meta"]

PY = sys.executable

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def run(cmd, fatal=True):
    log("RUN", " ".join(str(c) for c in cmd), CYAN)
    r = subprocess.run(cmd)
    if r.returncode != 0 and fatal:
        raise SystemExit(r.returncode)
    return r.returncode

def ensure_dirs():
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# Arg parse
# ─────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Step1: Fetch lyrics + audio")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--slug", help="Optional explicit slug (else derived from title)")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Auto-select first YouTube result (non-interactive)",
    )
    return p.parse_args()

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    args = parse_args()

    artist = args.artist.strip()
    title  = args.title.strip()
    slug   = (args.slug or slugify(title)).strip()

    ensure_dirs()

    txt_path  = TXT_DIR / f"{slug}.txt"
    mp3_path  = MP3_DIR / f"{slug}.mp3"
    meta_path = META_DIR / f"{slug}.json"

    log("FETCH", f'Artist="{artist}" Title="{title}" Slug="{slug}"', CYAN)

    # ─────────────────────────────
    # Lyrics (best-effort)
    # ─────────────────────────────
    if txt_path.exists():
        log("LYRICS", f"Exists → skipping {txt_path}", YELLOW)
    else:
        rc = run(
            [
                PY,
                SCRIPTS_DIR / "fetch_lyrics.py",
                "--artist", artist,
                "--title", title,
                "--out", str(txt_path),
            ],
            fatal=False,   # lyrics never fatal
        )
        if rc == 0 and txt_path.exists() and txt_path.stat().st_size > 0:
            log("LYRICS", f"Wrote {txt_path}", GREEN)
        else:
            log("LYRICS", "No lyrics found (empty file allowed)", YELLOW)
            txt_path.touch(exist_ok=True)

    # ─────────────────────────────
    # Audio (required)
    # ─────────────────────────────
    if mp3_path.exists():
        log("AUDIO", f"Exists → skipping {mp3_path}", YELLOW)
    else:
        cmd = [
            PY,
            SCRIPTS_DIR / "fetch_audio.py",
            "--query", f"{artist} {title}",
            "--out", str(mp3_path),
        ]
        if args.yes:
            cmd.append("--yes")

        run(cmd, fatal=True)

        if not mp3_path.exists():
            log("AUDIO", "MP3 missing after download", RED)
            raise SystemExit(1)

        log("AUDIO", f"Wrote {mp3_path}", GREEN)

    # ─────────────────────────────
    # Meta (always rewrite)
    # ─────────────────────────────
    meta = {
        "slug": slug,
        "artist": artist,
        "title": title,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lyrics_path": str(txt_path),
        "audio_path": str(mp3_path),
    }

    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log("META", f"Wrote {meta_path}", GREEN)

    log("STEP1", "Fetch complete", GREEN)

if __name__ == "__main__":
    main()

# end of 1_fetch.py
