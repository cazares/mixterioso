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
import sys
import time
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
# Bootstrap sys.path for mix_utils
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    PATHS, read_json, ask_yes_no, slugify,
)

OUT_DIR  = PATHS["output"]
META_DIR = PATHS["meta"]

# Load .env (for YOUTUBE_CLIENT_SECRETS_JSON, etc.)
load_dotenv()

# Scope required for uploading videos
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]

# Preset endings for titles
SUFFIX_PRESETS = {
    "1": "Karaoke",
    "2": "Lyrics",
    "3": "Letra",
    "4": "No bass",
    "5": "Car Karaoke – 35% reduced vocals",
    "6": "Car Karaoke – 25% reduced vocals",
    "7": "Car Karaoke – 15% reduced vocals",
    "8": "Sin guitarrón",
    "9": "No drums",
    "10": "No guitar",
}


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
def load_meta_for_slug(slug: str) -> dict | None:
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return None
    return read_json(meta_path) or None


def auto_main_title(slug: str, meta: dict | None) -> str:
    """
    Auto-generate the main (pre-parenthesis) title portion.
    Prefer "Artist – Title" from meta; fall back to nicely formatted slug.
    """
    if meta:
        artist = (meta.get("artist") or "").strip()
        title = (meta.get("title") or "").strip()
        if artist and title:
            return f"{artist} – {title}"
        if title:
            return title
    pretty = slug.replace("_", " ").title()
    return pretty


def build_tags(meta: dict | None) -> list[str]:
    """
    Simple, predictable tags.
    """
    tags = ["karaoke", "lyrics"]
    if meta:
        artist = (meta.get("artist") or "").strip()
        title  = (meta.get("title") or "").strip()
        if artist:
            tags.append(artist)
        if title:
            tags.append(title)
    # Deduplicate while preserving order
    seen = set()
    out = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def choose_suffix_with_presets(main_title: str) -> str:
    """
    Show preset endings and let the user choose one.
    Returns the chosen ending (without parentheses).
    """
    print()
    print("Choose an ending (the text inside parentheses).")
    print("Examples using your main title:")
    for key in sorted(SUFFIX_PRESETS.keys(), key=lambda k: int(k)):
        ending = SUFFIX_PRESETS[key]
        example = f"{main_title} ({ending})"
        print(f"  {key}) {ending}")
        print(f"     e.g. {example}")
    print("  99) Custom ending (you type it)")
    print()

    while True:
        try:
            choice = input("Enter choice [1-10 or 99]: ").strip()
        except EOFError:
            choice = ""
        if choice in SUFFIX_PRESETS:
            return SUFFIX_PRESETS[choice]
        if choice == "99":
            try:
                custom = input("Enter your ending (no parentheses): ").strip()
            except EOFError:
                custom = ""
            if not custom:
                print("Ending cannot be empty. Try again.")
                continue
            return custom
        print("Invalid choice. Please enter 1-10 or 99.")


def choose_title(slug: str, meta: dict | None) -> str:
    """
    Full title builder with four modes:

    1) Auto main title + preset ending
    2) Auto main title + custom ending
    3) Custom main title + preset ending
    4) Full custom title (replace everything)
    """
    auto_main = auto_main_title(slug, meta)

    while True:
        print()
        print("Title builder options (examples use your auto main title):")
        print(f"  Auto main title: {auto_main}")
        print()
        print("  1) Auto main title + preset ending")
        print(f"       e.g. {auto_main} (Karaoke)")
        print("  2) Auto main title + custom ending")
        print(f"       e.g. {auto_main} (My special version)")
        print("  3) Custom main title + preset ending")
        print("       e.g. Mujer Hilandera – Live Remix (Lyrics)")
        print("  4) Full custom title (replace everything)")
        print("       e.g. Mujer Hilandera – Karaoke Version – 2025 HD")
        print()

        try:
            mode = input("Choose how to build the YouTube title [1-4]: ").strip()
        except EOFError:
            mode = ""

        if mode not in ("1", "2", "3", "4"):
            print("Invalid choice. Please enter 1, 2, 3, or 4.")
            continue

        # Mode 1: auto main + preset ending
        if mode == "1":
            main_title = auto_main
            ending = choose_suffix_with_presets(main_title)
            full_title = f"{main_title} ({ending})"

        # Mode 2: auto main + custom ending (no presets)
        elif mode == "2":
            main_title = auto_main
            print()
            print(f"Auto main title: {main_title}")
            print("Now type the ending yourself (no parentheses).")
            print(f"Example final title: {main_title} (Your ending here)")
            print()
            try:
                ending = input("Ending: ").strip()
            except EOFError:
                ending = ""
            if not ending:
                print("Ending cannot be empty. Let's start over.")
                continue
            full_title = f"{main_title} ({ending})"

        # Mode 3: custom main title + preset ending
        elif mode == "3":
            print()
            print("Type your main title (everything before parentheses).")
            print(f"Example using presets later: My Song Title (Karaoke)")
            print()
            try:
                main_title = input("Main title: ").strip()
            except EOFError:
                main_title = ""
            if not main_title:
                print("Main title cannot be empty. Let's start over.")
                continue
            ending = choose_suffix_with_presets(main_title)
            full_title = f"{main_title} ({ending})"

        # Mode 4: full custom title
        else:  # mode == "4"
            print()
            print("Full custom title mode.")
            print("You will type the entire YouTube title exactly as it should appear.")
            print("Nothing will be added or changed automatically.")
            print(f"Auto main title example (for reference only): {auto_main}")
            print()
            try:
                full_title = input("Enter full YouTube title: ").strip()
            except EOFError:
                full_title = ""
            if not full_title:
                print("Title cannot be empty. Let's start over.")
                continue

        print()
        print("Resulting title will be:")
        print(f"  {full_title}")
        print()
        if ask_yes_no("Use this title?", default_yes=True):
            return full_title
        print("Okay, let's choose again.")


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
        default="private",
        help="Privacy status for the video (default: private).",
    )

    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    # Resolve paths
    video_path = OUT_DIR / f"{slug}.mp4"
    if not video_path.exists():
        log("ERROR", f"MP4 file not found: {video_path}", RED)
        sys.exit(1)

    meta = load_meta_for_slug(slug)
    if meta:
        log("META", f"Loaded meta for '{slug}'", CYAN)
    else:
        log("META", f"No meta JSON found for '{slug}'", YELLOW)

    # Title selection flow
    title = choose_title(slug, meta)

    # Description: optional one-liner
    print()
    try:
        description = input("Enter description (optional, ENTER for empty): ").strip()
    except EOFError:
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

    # OAuth + API client
    secrets_path = load_secrets_path()
    creds = get_credentials(secrets_path)
    youtube = build("youtube", "v3", credentials=creds)

    # Upload video
    video_id = upload_video(
        youtube,
        video_path,
        title,
        description,
        tags,
        category_id="10",  # Music
        privacy=args.privacy,
    )

    # Thumbnail: auto from 0.5s
    thumb_path = video_path.with_suffix(".jpg")
    try:
        extract_thumbnail(video_path, thumb_path, time_sec=0.5)
        set_thumbnail(youtube, video_id, thumb_path)
    except Exception as e:
        log("THUMB", f"Thumbnail failed: {e}", YELLOW)

    log("DONE", f"Video available at: https://youtube.com/watch?v={video_id}", GREEN)


if __name__ == "__main__":
    main()

# end of 5_upload.py
