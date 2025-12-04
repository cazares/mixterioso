#!/usr/bin/env python3
# api_server.py
#
# Lightweight REST API wrapper around scripts/0_master.py.
#
# Endpoints:
#   GET  /search?q=...          -> returns YouTube search results for a query
#   POST /render                -> runs the karaoke pipeline and returns MP4 or JSON
#
# This file should be placed in the *project root* (the parent of the "scripts" folder).

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request, send_file

try:
    import requests
except ImportError:
    requests = None  # handled at runtime

BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / "scripts"
OUTPUT_DIR = BASE_DIR / "output"

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def youtube_api_search(query: str, yt_key: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Use YouTube Data API v3 to search for videos."""
    if requests is None:
        raise RuntimeError("The 'requests' package is required for YouTube API search.")

    from urllib.parse import urlencode

    search_url = (
        "https://www.googleapis.com/youtube/v3/search?"
        + urlencode({
            "key": yt_key,
            "q": query,
            "type": "video",
            "part": "id,snippet",
            "maxResults": max_results,
        })
    )
    r = requests.get(search_url, timeout=10)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        return []

    video_ids = [it["id"]["videoId"] for it in items]

    stats_url = (
        "https://www.googleapis.com/youtube/v3/videos?"
        + urlencode({
            "key": yt_key,
            "id": ",".join(video_ids),
            "part": "statistics,snippet",
        })
    )
    r2 = requests.get(stats_url, timeout=10)
    r2.raise_for_status()
    stats_data = r2.json()
    indexed = {x.get("id"): x for x in stats_data.get("items", [])}

    out: List[Dict[str, Any]] = []
    for it in items:
        vid = it["id"]["videoId"]
        node = indexed.get(vid, {})
        snippet = node.get("snippet", {})
        stats   = node.get("statistics", {})
        title   = snippet.get("title") or "(no title)"
        views   = int(stats.get("viewCount", 0)) if stats else 0
        channel = snippet.get("channelTitle") or ""
        out.append({
            "videoId": vid,
            "title": title,
            "views": views,
            "channel": channel,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return out


def youtube_fallback_yt_dlp(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Use yt-dlp's ytsearch as a fallback if no API key is set."""
    try:
        cmd = ["yt-dlp", "-j", f"ytsearch{limit}:{query}"]
        out = subprocess.check_output(cmd, text=True)
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if "title" not in d or "id" not in d:
            continue
        vid   = d.get("id")
        title = d.get("title", "(no title)")
        views = d.get("view_count") or 0
        url   = d.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        results.append({
            "videoId": vid,
            "title": title,
            "views": views,
            "channel": d.get("uploader") or "",
            "url": url,
        })
    return results[:limit]


def find_latest_mp4(slug: str, profile: str) -> Optional[Path]:
    """Return the newest MP4 for given slug+profile, or None."""
    pattern = f"{slug}_{profile}_offset_*.mp4"
    candidates = list(OUTPUT_DIR.glob(pattern))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/search", methods=["GET"])
def search() -> Any:
    """Search YouTube for a query string and return candidate videos."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "Missing required query parameter 'q'."}), 400

    yt_key = os.getenv("YOUTUBE_API_KEY")
    try:
        if yt_key:
            results = youtube_api_search(query, yt_key, max_results=8)
        else:
            results = youtube_fallback_yt_dlp(query, limit=8)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    slug = slugify(query)
    return jsonify({
        "ok": True,
        "query": query,
        "slug": slug,
        "results": results,
    })

@app.route("/render", methods=["POST"])
def render() -> Any:
    """
    Kick off the main pipeline and either:
    - return the MP4 file (action=mp4), or
    - run the full pipeline including upload (action=upload).

    Flow:
      1) Run scripts/1_txt_mp3.py with positional query (if provided) in --no-ui mode.
      2) Then call scripts/0_master.py for steps 2–4 (or 2–5).
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON body."}), 400

    query   = (payload.get("query") or "").strip()
    slug    = (payload.get("slug") or "").strip()
    action  = (payload.get("action") or "").strip().lower()
    profile = (payload.get("profile") or "karaoke").strip()

    if action not in ("mp4", "upload"):
        return jsonify({"ok": False, "error": "Field 'action' must be 'mp4' or 'upload'."}), 400

    if not query and not slug:
        return jsonify({"ok": False, "error": "Provide at least one of 'query' or 'slug'."}), 400

    # Derive slug from query if missing
    if query and not slug:
        slug = slugify(query)

    # ------------------------------------------------------------------
    # STEP 1: ensure txt+mp3 via 1_txt_mp3.py in --no-ui mode
    # ------------------------------------------------------------------
    txt_mp3_script = SCRIPTS_DIR / "1_txt_mp3.py"
    if not txt_mp3_script.exists():
        return jsonify({"ok": False, "error": f"Could not find 1_txt_mp3.py at {txt_mp3_script}"}), 500

    # Only run step 1 automatically if we have a query (this is what 1_txt_mp3.py needs in --no-ui).
    if query:
        # NOTE: query is positional, not --query
        step1_cmd: List[str] = [
            sys.executable,
            str(txt_mp3_script),
        ]
        if slug:
            step1_cmd += ["--slug", slug]
        step1_cmd.append("--no-ui")
        step1_cmd.append(query)  # positional query argument

        if slug:
            step1_cmd += ["--slug", slug]
        step1_cmd.append("--no-ui")
        step1_cmd.append(query)  # positional query argument

        try:
            step1_proc = subprocess.run(
                step1_cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to start step1 (1_txt_mp3.py): {e}"}), 500

        if step1_proc.returncode != 0:
            return jsonify({
                "ok": False,
                "error": "Step1 (1_txt_mp3.py) exited with non-zero status.",
                "returncode": step1_proc.returncode,
                "stdout": step1_proc.stdout,
                "stderr": step1_proc.stderr,
                "command": step1_cmd,
            }), 500

    # ------------------------------------------------------------------
    # STEP 2–4 (or 2–5): use 0_master.py without re-running step1
    # ------------------------------------------------------------------
    master_path = SCRIPTS_DIR / "0_master.py"
    if not master_path.exists():
        return jsonify({"ok": False, "error": f"Could not find 0_master.py at {master_path}"}), 500

    steps = "234" if action == "mp4" else "2345"

    master_cmd: List[str] = [
        sys.executable,
        str(master_path),
        "--slug", slug,
        "--profile", profile,
        "--no-ui",
        "--steps", steps,
    ]

    try:
        master_proc = subprocess.run(
            master_cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to start 0_master.py: {e}"}), 500

    if master_proc.returncode != 0:
        return jsonify({
            "ok": False,
            "error": "Pipeline exited with non-zero status.",
            "returncode": master_proc.returncode,
            "stdout": master_proc.stdout,
            "stderr": master_proc.stderr,
            "command": master_cmd,
        }), 500

    if action == "upload":
        # Upload is handled by step 5 inside 0_master.py
        return jsonify({
            "ok": True,
            "status": "upload_complete",
            "slug": slug,
            "profile": profile,
        })

    # action == "mp4": find latest MP4 and send it back
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    mp4_path = find_latest_mp4(slug, profile)
    if not mp4_path or not mp4_path.exists():
        return jsonify({
            "ok": False,
            "error": f"MP4 not found for slug={slug}, profile={profile}",
            "stdout": master_proc.stdout,
            "stderr": master_proc.stderr,
            "command": master_cmd,
        }), 500

    return send_file(
        str(mp4_path),
        mimetype="video/mp4",
        as_attachment=True,
        download_name=mp4_path.name,
    )


def main() -> None:
    port = int(os.getenv("API_PORT", "8000"))
    # host=0.0.0.0 allows access from Codespaces / other machines
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()

# end of api_server.py
