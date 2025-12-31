#!/usr/bin/env python3
"""
Minimal YouTube uploader for Mixterioso.

Usage:
    python3 scripts/5_upload.py --slug mujer_hilandera

Requirements:
    - Environment variable YOUTUBE_CLIENT_SECRETS_JSON must point to:
        * client_secret.json  OR
        * a directory containing client_secret.json

    - OAuth token will be stored as youtube_token.json next to client_secret.json
"""

import argparse
import os
import re
import sys
import subprocess
from pathlib import Path

from dotenv import load_dotenv

# Google API imports
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ─────────────────────────────────────────────
# Bootstrap sys.path for scripts.common import
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.common import (
    log, CYAN, GREEN, YELLOW, RED,
    slugify,
)

OUT_DIR  = ROOT / "output"
META_DIR = ROOT / "meta"
TIMINGS_DIR = ROOT / "timings"

def read_json(path: Path) -> dict | None:
    try:
        import json as _json
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        ans = input(f"{prompt} {suffix}: ").strip().lower()
    except EOFError:
        ans = ""
    if ans == "" and default_yes:
        return True
    if ans == "" and not default_yes:
        return False
    return ans in ("y", "yes")

def open_path(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.run(["start", str(path)], shell=True)
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception as e:
        log("OPEN", f"Failed to open {path}: {e}", YELLOW)

# Load .env (for YOUTUBE_CLIENT_SECRETS_JSON, etc.)
load_dotenv()

# Scope required for uploading videos
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


# ─────────────────────────────────────────────
# Secrets / OAuth helpers
# ─────────────────────────────────────────────
def load_secrets_path() -> Path:
    """
    Resolve the location of client_secret.json based on YOUTUBE_CLIENT_SECRETS_JSON.

    Accepts:
      - exact file path to client_secret.json
      - directory containing client_secret.json
    """
    raw = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON")

    if not raw:
        log("SECRETS", "YOUTUBE_CLIENT_SECRETS_JSON is not set.", RED)
        sys.exit(1)

    p = Path(raw).expanduser()

    if p.is_file():
        return p

    if p.is_dir():
        guess = p / "client_secret.json"
        if guess.exists():
            return guess

    log("SECRETS", f"Invalid secrets path: {p}", RED)
    sys.exit(1)


def get_credentials(secrets_path: Path):
    """
    Get or create OAuth credentials for the YouTube upload scope.

    Token is stored as youtube_token.json next to client_secret.json.
    """
    token_path = secrets_path.parent / "youtube_token.json"
    creds = None

    # Try to load existing token
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path),
                YOUTUBE_UPLOAD_SCOPE,
            )
        except Exception:
            creds = None

    # Refresh or run new OAuth flow if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                log("OAUTH", "Refreshing existing OAuth token...", CYAN)
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            log("OAUTH", "Running OAuth login flow...", CYAN)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets_path),
                scopes=YOUTUBE_UPLOAD_SCOPE,
            )
            # This opens a browser and listens on localhost
            creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            log("OAUTH", f"Saved OAuth token to {token_path}", GREEN)

    return creds


# ─────────────────────────────────────────────
# Thumbnail helper
# ─────────────────────────────────────────────
def extract_thumbnail(video_path: Path, out_path: Path, time_sec: float) -> None:
    """
    Extract a JPEG thumbnail from the given time position using ffmpeg.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(time_sec),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    log("THUMB", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)


# ─────────────────────────────────────────────
# Upload logic
# ─────────────────────────────────────────────
def upload_video(
    youtube,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy: str,
) -> str:
    """
    Perform the actual YouTube upload and return the new video ID.
    """
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)

    log("UPLOAD", f"Starting upload: {video_path}", CYAN)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                log("UPLOAD", f"Progress: {pct}%", CYAN)
        except HttpError as e:
            log("ERROR", f"Upload failed: {e}", RED)
            raise

    video_id = response.get("id")
    log("UPLOAD", f"Upload complete. video_id={video_id}", GREEN)
    return video_id


def set_thumbnail(youtube, video_id: str, thumb_path: Path) -> None:
    """
    Upload a thumbnail for a video.
    """
    log("THUMB", f"Uploading thumbnail for {video_id}: {thumb_path}", CYAN)
    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
    request = youtube.thumbnails().set(videoId=video_id, media_body=media)
    _ = request.execute()
    log("THUMB", "Thumbnail set.", GREEN)


# ─────────────────────────────────────────────
# Title / meta helpers
# ─────────────────────────────────────────────
def _infer_artist_title_from_lrc(slug: str) -> dict | None:
    """Try to read [ar:...] and [ti:...] tags from timings/<slug>.lrc."""
    lrc_path = TIMINGS_DIR / f"{slug}.lrc"
    if not lrc_path.exists():
        return None

    artist = ""
    title = ""

    tag_re = re.compile(r"^\[([a-zA-Z]{2,10})\s*:\s*(.*?)\s*\]\s*$")
    try:
        lines = lrc_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None

    for raw in lines[:60]:
        s = raw.strip()
        if not s.startswith("[") or "]" not in s:
            continue
        m = tag_re.match(s)
        if not m:
            continue
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if not val:
            continue

        if key in ("ar", "artist", "art"):
            artist = val
        elif key in ("ti", "title"):
            title = val
        elif key in ("au", "author") and not artist:
            artist = val

        if artist and title:
            break

    if not (artist or title):
        return None

    return {"artist": artist, "title": title, "_meta_path": str(lrc_path)}


def load_meta_for_slug(slug: str) -> dict | None:
    """Load best-effort metadata for a slug."""
    candidates = [
        META_DIR / f"{slug}.json",
        META_DIR / f"{slug}.step1.json",
        META_DIR / f"{slug}.step2.json",
        META_DIR / f"{slug}.step3.json",
        META_DIR / f"{slug}.step4.json",
        META_DIR / f"{slug}.step5.json",
    ]

    best_any: dict | None = None

    for p in candidates:
        if p.exists():
            j = read_json(p)
            knows = isinstance(j, dict)
            if knows:
                j["_meta_path"] = str(p)
                artist = (j.get("artist") or "").strip()
                title = (j.get("title") or "").strip()
                if artist and title:
                    return j
                if best_any is None:
                    best_any = j

    try:
        extras = sorted(
            META_DIR.glob(f"{slug}*.json"),
            key=lambda pp: pp.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        extras = []

    for p in extras:
        if str(p) in {str(x) for x in candidates}:
            continue
        j = read_json(p)
        if isinstance(j, dict):
            j["_meta_path"] = str(p)
            artist = (j.get("artist") or "").strip()
            title = (j.get("title") or "").strip()
            if artist and title:
                return j
            if best_any is None:
                best_any = j

    if best_any is not None:
        return best_any

    return _infer_artist_title_from_lrc(slug)


def auto_main_title(slug: str, meta: dict | None) -> str:
    """Return base title in the required format: 'Artist - Title' when possible."""
    if isinstance(meta, dict):
        artist = (meta.get("artist") or "").strip()
        title = (meta.get("title") or "").strip()
        if artist and title:
            return f"{artist} - {title}"
        if title:
            return title
    return slug.replace("_", " ").title()


def build_tags(meta: dict | None) -> list[str]:
    """Simple, predictable tags."""
    tags = ["karaoke", "lyrics"]
    if isinstance(meta, dict):
        artist = (meta.get("artist") or "").strip()
        title  = (meta.get("title") or "").strip()
        if artist:
            tags.append(artist)
        if title:
            tags.append(title)

    seen = set()
    out = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _parse_percent(value) -> int | None:
    """Parse a percent-ish value into int 0..100, or None if unknown."""
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        v = float(value)
        if 0.0 <= v <= 1.0:
            return int(round(v * 100))
        if 0.0 <= v <= 100.0:
            return int(round(v))
        return None

    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return None
        m = re.search(r"(\d{1,3})\s*%?", s)
        if m:
            try:
                n = int(m.group(1))
                if 0 <= n <= 100:
                    return n
            except Exception:
                return None
    return None


def _find_first_percent(meta: dict, candidates: list[str]) -> int | None:
    for k in candidates:
        if k in meta:
            p = _parse_percent(meta.get(k))
            if p is not None:
                if "reduc" in k and "level" not in k and "pct" in k:
                    return max(0, min(100, 100 - p))
                return p
    return None


def _infer_stem_pcts(meta: dict | None) -> tuple[int | None, int | None]:
    """Return (vocals_pct, bass_pct) if discoverable from meta."""
    if not isinstance(meta, dict):
        return (None, None)

    vocals_keys = [
        "vocals_pct", "vocals_percent", "vocals_percentage",
        "vocals_level_pct", "vocals_level", "vocals_volume",
        "vocals_gain_pct", "vocals_mix_pct",
        "reduced_vocals_pct", "vocals_reduction_pct", "reduce_vocals_pct",
    ]

    bass_keys = [
        "bass_pct", "bass_percent", "bass_percentage",
        "bass_level_pct", "bass_level", "bass_volume",
        "bass_gain_pct", "bass_mix_pct",
        "reduced_bass_pct", "bass_reduction_pct", "reduce_bass_pct",
    ]

    vocals_pct = _find_first_percent(meta, vocals_keys)
    bass_pct = _find_first_percent(meta, bass_keys)

    for container_key in ("stems", "stem_levels", "mix", "levels"):
        container = meta.get(container_key)
        if isinstance(container, dict):
            if vocals_pct is None:
                vocals_pct = _find_first_percent(container, vocals_keys + ["vocals"])
            if bass_pct is None:
                bass_pct = _find_first_percent(container, bass_keys + ["bass"])

    return (vocals_pct, bass_pct)


def suggest_ending_from_stems(meta: dict | None) -> str | None:
    """Build an ending like: '35% Vocals, No Bass' or 'Karaoke'."""
    vocals_pct, bass_pct = _infer_stem_pcts(meta)

    parts: list[str] = []

    if vocals_pct is not None:
        if vocals_pct == 0:
            parts.append("Karaoke")
        else:
            parts.append(f"{vocals_pct}% Vocals")

    if bass_pct is not None:
        if bass_pct == 0:
            parts.append("No Bass")

    if not parts:
        return None
    return ", ".join(parts)


def choose_title(slug: str, meta: dict | None) -> str:
    main_title = auto_main_title(slug, meta)
    suggested = suggest_ending_from_stems(meta)

    print()
    print(f"Base title: {main_title}")
    if suggested:
        print(f"Suggested ending: {suggested}")
    print()

    while True:
        try:
            ending = input("Custom ending: ").strip()
        except EOFError:
            ending = ""
        if not ending:
            print("Ending cannot be empty. Try again.")
            continue
        return f"{main_title} ({ending})"


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Upload Mixterioso MP4 to YouTube (minimal interface).")

    p.add_argument(
        "--slug",
        required=True,
        help="Slug for the song (e.g. 'mujer_hilandera').",
    )
    p.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default="unlisted",
        help="Privacy status for the video (default: unlisted).",
    )

    return p.parse_args(argv)


def _resolve_video_path(slug: str) -> Path:
    direct = OUT_DIR / f"{slug}.mp4"
    if direct.exists():
        return direct

    matches = sorted(OUT_DIR.glob(f"{slug}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        log("VIDEO", f"MP4 not found at {direct}; using newest match: {matches[0]}", YELLOW)
        return matches[0]

    return direct


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    if args.privacy != "unlisted":
        log("PRIV", f"Ignoring --privacy '{args.privacy}' (forcing unlisted)", YELLOW)
        args.privacy = "unlisted"

    video_path = _resolve_video_path(slug)
    if not video_path.exists():
        log("ERROR", f"MP4 file not found: {video_path}", RED)
        sys.exit(1)

    meta = load_meta_for_slug(slug)
    if meta:
        src = meta.get("_meta_path") if isinstance(meta, dict) else None
        log("META", f"Loaded meta for '{slug}'" + (f" ({src})" if src else ""), CYAN)
    else:
        log("META", f"No meta JSON found for '{slug}'", YELLOW)

    title = choose_title(slug, meta)

    description = ""

    tags = build_tags(meta)

    print()
    log("SUMMARY", "YouTube upload configuration:", CYAN)
    print(f"  File      : {video_path}")
    print(f"  Title     : {title}")
    print(f"  Privacy   : {args.privacy}")
    print(f"  Tags      : {', '.join(tags) if tags else '(none)'}")
    print(f"  Description length: {len(description)} chars")
    print()

    if not ask_yes_no("Proceed with upload?", default_yes=True):
        log("ABORT", "User cancelled upload.", YELLOW)
        sys.exit(0)

    secrets_path = load_secrets_path()
    creds = get_credentials(secrets_path)
    youtube = build("youtube", "v3", credentials=creds)

    video_id = upload_video(
        youtube,
        video_path,
        title,
        description,
        tags,
        category_id="10",  # Music
        privacy=args.privacy,
    )

    thumb_path = video_path.with_suffix(".jpg")
    try:
        extract_thumbnail(video_path, thumb_path, time_sec=0.5)
        set_thumbnail(youtube, video_id, thumb_path)
    except Exception as e:
        log("THUMB", f"Thumbnail failed: {e}", YELLOW)

    log("DONE", f"Video available at: https://youtube.com/watch?v={video_id}", GREEN)

    open_path(OUT_DIR)


if __name__ == "__main__":
    main()

# end of 5_upload.py
