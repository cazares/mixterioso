#!/usr/bin/env python3
import sys
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path (repo root)
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

"""
Step1 orchestrator: fetch all assets needed before stems/timing/video.

Outputs:
- txts/<slug>.txt
- mp3s/<slug>.mp3
- timings/<slug>.lrc
- timings/<slug>.<lang>.vtt
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
import re

def _slugify_title(title: str) -> str:
    s = (title or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "song"

def parse_query(q: str) -> tuple[str, str]:
    q = (q or "").strip().strip('"').strip("'").strip()
    q_norm = q.replace("—", "-").replace("–", "-")
    if " - " in q_norm:
        a, t = q_norm.split(" - ", 1)
    elif "-" in q_norm:
        a, t = q_norm.split("-", 1)
    elif ":" in q_norm:
        a, t = q_norm.split(":", 1)
    else:
        return ("", q)
    return (a.strip(), t.strip())

def _run(tag: str, cmd: list[str]) -> int:
    t0 = time.perf_counter()
    p = subprocess.run(cmd)
    dt = time.perf_counter() - t0
    print(f"[{tag}] exit={p.returncode} dt={dt:.2f}s")
    return p.returncode

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Step1: fetch lyrics/audio/LRC/captions")
    ap.add_argument("--query", help='Convenience: "Artist - Title"')
    ap.add_argument("--artist", help="Artist name")
    ap.add_argument("--title", help="Song title")
    ap.add_argument("--slug", help="Slug (defaults to slugified title)")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-txt", action="store_true")
    ap.add_argument("--no-lrc", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    return ap.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)

    artist = (args.artist or "").strip()
    title = (args.title or "").strip()

    if args.query:
        qa, qt = parse_query(args.query)
        if not artist:
            artist = qa
        if not title:
            title = qt

    if not title:
        raise SystemExit("Step1 requires --title or --query")

    slug = (args.slug or "").strip() or _slugify_title(title)

    scripts_dir = Path(__file__).resolve().parent
    py = sys.executable

    t_start = time.perf_counter()

    if not args.no_audio:
        rc = _run("audio", [py, str(scripts_dir/"1_fetch_audio_mp3.py"), "--artist", artist, "--title", title, "--slug", slug])
        if rc != 0:
            raise SystemExit(rc)

    if not args.no_txt:
        rc = _run("txt", [py, str(scripts_dir/"1_fetch_lyrics_txt.py"), "--artist", artist, "--title", title, "--slug", slug])
        if rc != 0:
            print("[txt] failed (continuing)")

    if not args.no_lrc:
        rc = _run("lrc", [py, str(scripts_dir/"1_fetch_lyrics_lrc.py"), "--artist", artist, "--title", title, "--slug", slug])
        if rc != 0:
            print("[lrc] failed (continuing)")

    if not args.no_captions:
        rc = _run("captions", [py, str(scripts_dir/"1_fetch_captions_vtt.py"), "--artist", artist, "--title", title, "--slug", slug])
        if rc != 0:
            print("[captions] failed (continuing)")

    dt = time.perf_counter() - t_start
    print(f"[SUMMARY] Step1 completed in {dt:.2f}s")
    print(json.dumps({"type": "result", "slug": slug}, ensure_ascii=False))

if __name__ == "__main__":
    main()
# end of 1_fetch.py
