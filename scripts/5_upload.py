#!/usr/bin/env python3
"""
Clean, modern YouTube uploader for Mixterioso.

- Uses google-auth / google-auth-oauthlib / google-api-python-client
- No oauth2client / run_flow / Storage
- Stores OAuth token as youtube_token.json next to client_secret.json

Environment:
    YOUTUBE_CLIENT_SECRETS_JSON -> path to client_secret.json
                                  OR a directory containing client_secret.json

Example:
    export YOUTUBE_CLIENT_SECRETS_JSON="/Users/you/karaoke/client_secret.json"
    python3 scripts/5_upload.py --file output/foo_bar.mp4 --title "Foo – Bar (Karaoke)"
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Load .env (for YOUTUBE_CLIENT_SECRETS_JSON, etc.)
load_dotenv()

# -----------------------------------------------------------------------------
# Constants / paths
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# Scope required for uploading videos
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def log(section: str, msg: str) -> None:
    """Simple timestamped logger."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{section}] {msg}")


def load_secrets_path() -> Path:
    """
    Resolve the location of client_secret.json based on YOUTUBE_CLIENT_SECRETS_JSON.

    Accepts:
      - exact file path to client_secret.json
      - directory containing client_secret.json
    """
    raw = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON")

    if not raw:
        log("SECRETS", "YOUTUBE_CLIENT_SECRETS_JSON is not set.")
        sys.exit(1)

    p = Path(raw).expanduser()

    if p.is_file():
        return p

    if p.is_dir():
        guess = p / "client_secret.json"
        if guess.exists():
            return guess

    log("SECRETS", f"Invalid secrets path: {p}")
    sys.exit(1)


def get_credentials(secrets_path: Path):
    """
    Get or create OAuth credentials for the YouTube upload scope.

    Token is stored as youtube_token.json next to client_secret.json (Option A).
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
                log("OAUTH", "Refreshing existing OAuth token...")
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            log("OAUTH", "Running OAuth login flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets_path),
                scopes=YOUTUBE_UPLOAD_SCOPE,
            )
            # This opens a browser and listens on localhost
            creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            log("OAUTH", f"Saved OAuth token to {token_path}")

    return creds


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
    log("THUMB", " ".join(cmd))
    subprocess.run(cmd, check=True)


# -----------------------------------------------------------------------------
# Upload logic
# -----------------------------------------------------------------------------
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

    log("UPLOAD", f"Starting upload: {video_path}")
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
                log("UPLOAD", f"Progress: {pct}%")
        except HttpError as e:
            log("ERROR", f"Upload failed: {e}")
            raise

    video_id = response.get("id")
    log("UPLOAD", f"Upload complete. video_id={video_id}")
    return video_id


def set_thumbnail(youtube, video_id: str, thumb_path: Path) -> None:
    """
    Upload a thumbnail for a video.
    """
    log("THUMB", f"Uploading thumbnail for {video_id}: {thumb_path}")
    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
    request = youtube.thumbnails().set(videoId=video_id, media_body=media)
    _ = request.execute()
    log("THUMB", "Thumbnail set.")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Upload an MP4 file to YouTube.")

    p.add_argument("--file", required=True, help="Path to MP4 file.")
    p.add_argument("--title", default=None, help="YouTube video title.")
    p.add_argument(
        "--description",
        default="",
        help="YouTube video description.",
    )
    p.add_argument(
        "--tags",
        type=str,
        default="",
        help="Comma-separated tags, e.g. 'karaoke,red hot chili peppers'.",
    )
    p.add_argument(
        "--category-id",
        type=str,
        default="10",
        help="YouTube category (default '10' = Music).",
    )
    p.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default="unlisted",
        help="Privacy status for the video.",
    )
    p.add_argument(
        "--thumb-from-sec",
        type=float,
        default=None,
        help="If set, extract and upload a thumbnail at this second.",
    )

    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    video_path = Path(args.file).resolve()
    if not video_path.exists():
        log("ERROR", f"Video file not found: {video_path}")
        sys.exit(1)

    title = args.title or video_path.stem
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    secrets_path = load_secrets_path()
    creds = get_credentials(secrets_path)
    youtube = build("youtube", "v3", credentials=creds)

    # Upload video
    video_id = upload_video(
        youtube,
        video_path,
        title,
        args.description,
        tags,
        args.category_id,
        args.privacy,
    )

    # Thumbnail (optional)
    if args.thumb_from_sec is not None:
        thumb_path = video_path.with_suffix(".jpg")
        extract_thumbnail(video_path, thumb_path, args.thumb_from_sec)
        set_thumbnail(youtube, video_id, thumb_path)

    log("DONE", f"Video available at: https://youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()

# end of 5_upload.py
