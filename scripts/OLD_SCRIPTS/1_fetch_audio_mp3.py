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
Fetch audio MP3 from YouTube via yt-dlp.

- Searches top N results
- Locally sorts by view_count desc for display
- Chooses a duration-cluster and picks most-viewed within cluster
- Downloads audio as mp3s/<slug>.mp3 (overwrites)

Requires: yt-dlp on PATH
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mix_utils import PATHS, log, CYAN, GREEN, YELLOW, RED
except Exception:
    def log(tag, msg, color=""):
        print(f"[{tag}] {msg}")
    CYAN = GREEN = YELLOW = RED = ""
    ROOT = Path(__file__).resolve().parent.parent
    PATHS = {"mp3": ROOT / "mp3s"}

MP3_DIR = Path(PATHS["mp3"])

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Fetch MP3 from YouTube")
    ap.add_argument("--artist", default="")
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--tolerance-secs", type=float, default=2.5)
    return ap.parse_args(argv)

def _safe_int(x) -> int:
    try:
        if x is None:
            return 0
        return int(float(str(x).strip()))
    except Exception:
        return 0

def _safe_float(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(str(x).strip())
    except Exception:
        return 0.0

def yt_search(query: str, top_n: int) -> List[Dict[str, Any]]:
    cmd = ["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", f"ytsearch{top_n}:{query}"]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        data = json.loads(out)
    except Exception as e:
        log("youtube", f"search failed: {e}", RED)
        return []
    entries = data.get("entries") or []
    results = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id") or ""
        url = e.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        if not vid or not url:
            continue
        results.append({
            "id": vid,
            "title": e.get("title") or "",
            "duration": _safe_float(e.get("duration")),
            "view_count": _safe_int(e.get("view_count")),
            "webpage_url": url,
        })
    return results

def pick_best_clustered(results: List[Dict[str, Any]], tolerance: float) -> Optional[Dict[str, Any]]:
    if not results:
        return None
    items = [r for r in results if (r.get("duration") or 0) > 0]
    if not items:
        return max(results, key=lambda r: r.get("view_count") or 0)
    items.sort(key=lambda r: r["duration"])
    clusters: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    anchor = None
    for r in items:
        d = float(r["duration"])
        if anchor is None:
            anchor = d
            cur = [r]
            continue
        if abs(d - anchor) <= tolerance:
            cur.append(r)
        else:
            clusters.append(cur)
            anchor = d
            cur = [r]
    if cur:
        clusters.append(cur)
    best_cluster = max(clusters, key=lambda c: (len(c), max((x.get("view_count") or 0) for x in c)))
    return max(best_cluster, key=lambda r: r.get("view_count") or 0)

def download_mp3(url: str, slug: str) -> Path:
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MP3_DIR / f"{slug}.mp3"
    out_tmpl = str(MP3_DIR / f"{slug}.%(ext)s")
    cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0", "-o", out_tmpl, "--no-playlist", url]
    log("youtube:dl", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)
    if not out_path.exists():
        for c in sorted(MP3_DIR.glob(f"{slug}*.mp3")):
            if c != out_path:
                c.replace(out_path)
                break
    return out_path

def fmt_views(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def main(argv=None):
    args = parse_args(argv)
    query = f"{(args.artist or '').strip()} {(args.title or '').strip()}".strip()
    t0 = time.perf_counter()
    results = yt_search(query, args.top_n)
    if not results:
        raise SystemExit(1)

    display = sorted(results, key=lambda r: r.get("view_count") or 0, reverse=True)
    print("")
    print("Top results (sorted locally by views):")
    for i, r in enumerate(display[:5], 1):
        print(f"  {i}) {fmt_views(r['view_count']):>6}  {int(round(r.get('duration') or 0)):>4}s  {r.get('title','')[:80]}")

    best = pick_best_clustered(results, args.tolerance_secs)
    if not best:
        raise SystemExit(1)

    print("")
    print(f"Selected: {fmt_views(best['view_count'])}  {int(round(best.get('duration') or 0))}s  {best.get('title','')[:90]}")
    out = download_mp3(best["webpage_url"], args.slug)
    log("audio", f"Wrote {out} in {time.perf_counter()-t0:.2f}s", GREEN)

if __name__ == "__main__":
    main()
# end of 1_fetch_audio_mp3.py
