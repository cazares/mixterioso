#!/usr/bin/env python3
# scripts/5_upload.py
#
# Upload a video to YouTube via the official YouTube Data API.
#
# Requirements:
#   - OAuth client secrets JSON:
#          $YOUTUBE_CLIENT_SECRETS_JSON   (file OR directory)
#          ./client_secret.json           (fallback)
#   - Stores OAuth tokens next to client_secret.json as youtube_token.json
#
#   This script is intentionally simple: 0_master.py builds the final
#   title/description and calls this with --file/--title/etc.
#
# CLI:
#   --file            MP4 path (required)
#   --title           Title (required)
#   --description     Description text
#   --tags            Comma-separated list of tags
#   --category-id     YouTube category (default "10")
#   --privacy         public|unlisted|private
#   --made-for-kids   Mark as “made for kids”
#   --thumb-from-sec  Generate and upload thumbnail from timestamp (sec)
#
# Returns:
#   Prints the video ID JSON object on final line.

import argparse
import http.client
import httplib2
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import run_flow

# ==========================================================
# COLORS (match 0_master.py)
# ==========================================================
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
WHITE  = "\033[97m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"
MAG    = "\033[35m"


def log(section: str, msg: str, color: str = CYAN):
    print(f"{color}[{section}]{RESET} {msg}")


# ==========================================================
# LOCATE CLIENT SECRETS
# ==========================================================
def find_client_secret_json() -> Path:
    """
    Locate OAuth client secrets either from env:
        $YOUTUBE_CLIENT_SECRETS_JSON
    or fallback to ./client_secret.json
    """
    env = os.environ.get("YOUTUBE_CLIENT_SECRETS_JSON")
    if env:
        p = Path(env)
        if p.is_dir():
            p = p / "client_secret.json"
        if not p.exists():
            raise SystemExit(
                f"$YOUTUBE_CLIENT_SECRETS_JSON points to '{env}' "
                f"but no client_secret.json found inside it."
            )
        return p

    # Fallback: local directory
    p = Path("client_secret.json")
    if not p.exists():
        raise SystemExit(
            "client_secret.json not found.\n"
            "Place it in the project root OR set $YOUTUBE_CLIENT_SECRETS_JSON."
        )
    return p


# ==========================================================
# THUMBNAIL GENERATION
# ==========================================================
def generate_thumbnail(video_path: Path, sec: float) -> Optional[Path]:
    """
    Extract a thumbnail via ffmpeg at timestamp "sec".
    Returns:
        Path to thumbnail JPG or None on failure.
    """
    thumb = video_path.with_suffix(".jpg")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(sec),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(thumb)
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return thumb
    except Exception as e:
        log("UPLOAD", f"Thumbnail generation failed: {e}", YELLOW)
        return None


# ==========================================================
# YOUTUBE CLIENT AUTH
# ==========================================================
def get_authenticated_service() -> "Resource":
    """
    Perform OAuth2 from client_secret.json and return YouTube Data API client.
    Token is stored in same directory as youtube_token.json.
    """
    client_secret_path = find_client_secret_json()
    secrets_dir = client_secret_path.parent
    token_path = secrets_dir / "youtube_token.json"

    flow = flow_from_clientsecrets(
        str(client_secret_path),
        scope=["https://www.googleapis.com/auth/youtube.upload"],
        message="Unable to find client secrets.",
    )

    storage = Storage(str(token_path))
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        log("UPLOAD", "Launching OAuth flow in browser...", CYAN)
        credentials = run_flow(flow, storage)

    # Create API client
    return build("youtube", "v3", http=credentials.authorize(httplib2.Http()))


# ==========================================================
# PERFORM UPLOAD
# ==========================================================
def perform_upload(
    youtube,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy: str,
    made_for_kids: bool,
) -> str:
    """
    Upload the video file to YouTube.
    Returns:
        The video ID string.
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
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    log("UPLOAD", "Starting resumable upload...", CYAN)

    response = None
    error = None
    retry = 0
    max_retries = 10

    while response is None:
        try:
            status, response = request.next_chunk()
            if response and "id" in response:
                vid = response["id"]
                log("UPLOAD", f"Upload complete. Video ID: {vid}", GREEN)
                return vid
        except HttpError as e:
            error = f"HTTP error: {e.resp.status}"
        except (httplib2.HttpLib2Error, IOError, http.client.NotConnected):
            error = "Network error"

        if error:
            retry += 1
            if retry > max_retries:
                raise SystemExit(f"Upload failed: {error}")

            log("UPLOAD", f"{error}; retry {retry}/{max_retries}", YELLOW)
            error = None

    raise SystemExit("Upload failed with unknown error.")


# ==========================================================
# THUMBNAIL UPLOAD
# ==========================================================
def upload_thumbnail(youtube, video_id: str, thumb_path: Path):
    """
    Upload a generated thumbnail to YouTube.
    """
    log("UPLOAD", f"Uploading thumbnail: {thumb_path}", CYAN)

    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=media
        ).execute()
        log("UPLOAD", "Thumbnail uploaded.", GREEN)
    except Exception as e:
        log("UPLOAD", f"Thumbnail upload failed: {e}", YELLOW)


# ==========================================================
# MAIN
# ==========================================================
def parse_args():
    p = argparse.ArgumentParser(description="Upload MP4 to YouTube")
    p.add_argument("--file", required=True, help="MP4 to upload")
    p.add_argument("--title", required=True, help="YouTube title")
    p.add_argument("--description", default="", help="YouTube description")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--category-id", default="10", help="YouTube categoryId")
    p.add_argument(
        "--privacy",
        default="unlisted",
        choices=["public", "unlisted", "private"],
        help="Video privacy status",
    )
    p.add_argument(
        "--made-for-kids",
        action="store_true",
        help="Mark video as made for kids",
    )
    p.add_argument(
        "--thumb-from-sec",
        type=float,
        help="Generate and upload a thumbnail from timestamp (sec)",
    )
    return p.parse_args()


def main():
    args = parse_args()

    video_path = Path(args.file)
    if not video_path.exists():
        raise SystemExit(f"Video file not found: {video_path}")

    # Parse tags
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    youtube = get_authenticated_service()

    # Upload video
    video_id = perform_upload(
        youtube=youtube,
        video_path=video_path,
        title=args.title,
        description=args.description,
        tags=tags,
        category_id=args.category_id,
        privacy=args.privacy,
        made_for_kids=args.made_for_kids,
    )

    # Thumbnail selection
    if args.thumb_from_sec is not None:
        thumb = generate_thumbnail(video_path, args.thumb_from_sec)
        if thumb:
            upload_thumbnail(youtube, video_id, thumb)

    # Final success JSON
    print(json.dumps({"ok": True, "video_id": video_id}))


if __name__ == "__main__":
    main()

# end of 5_upload.py
