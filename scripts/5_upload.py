#!/usr/bin/env python3
# scripts/5_upload.py
import argparse
import os
import sys
import json
import subprocess
from pathlib import Path
from typing import List, Optional

# Graceful dependency handling so master can capture a clean error.
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except Exception as e:
    # Emit structured JSON so 0_master.py can stash it into the receipt.
    print(json.dumps({
        "ok": False,
        "error": "MissingYouTubeDeps",
        "message": (
            "YouTube client libraries are not installed in this environment. "
            "Install with: pip3 install --upgrade google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ),
        "detail": str(e),
    }))
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PATH = Path(".youtube_oauth_token.json")

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def log(section: str, msg: str, color: str = RESET):
    print(f"{color}[{section}]{RESET} {msg}")


def get_creds() -> Credentials:
    client_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON", "client_secret.json")
    if not Path(client_secrets).exists():
        # Print to stdout as JSON so master can parse this hint.
        print(json.dumps({
            "ok": False,
            "error": "MissingOAuthClientSecrets",
            "message": (
                "Missing OAuth client secrets JSON. Set env YOUTUBE_CLIENT_SECRETS_JSON to your Google OAuth client file "
                "or place client_secret.json in the working directory. API keys (YOUTUBE_API_KEY) cannot upload videos."
            ),
            "hint": "Create OAuth 2.0 Client ID (Desktop) at https://console.cloud.google.com/apis/credentials",
            "expected_env": "YOUTUBE_CLIENT_SECRETS_JSON=/path/to/client_secret.json",
        }))
        sys.exit(1)

    creds: Optional[Credentials] = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("auth", "Refreshing OAuth token...", YELLOW)
            creds.refresh(Request())
        else:
            log("auth", "Running local OAuth flow in your browser...", CYAN)
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        TOKEN_PATH.write_text(creds.to_json())

    return creds


def build_youtube(creds: Credentials):
    return build("youtube", "v3", credentials=creds)


def upload_video(
    youtube,
    file_path: Path,
    title: str,
    description: str,
    tags: List[str],
    category_id: str,
    privacy_status: str,
    made_for_kids: bool,
) -> str:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,  # default private
            "selfDeclaredMadeForKids": made_for_kids,  # default False unless flag passed
        },
    }

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    log("upload", f"Uploading: {file_path.name}", CYAN)
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"\r{pct}% uploaded", end="", flush=True)
        print()
    except HttpError as e:
        print()
        log("upload", f"HTTP error: {e}", RED)
        sys.exit(1)

    video_id = response.get("id")
    log("upload", f"Upload complete. Video ID: {video_id}", GREEN)
    return video_id


def extract_thumbnail_frame(mp4: Path, out_png: Path, timestamp_sec: float):
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp_sec}",
        "-i",
        str(mp4),
        "-vframes",
        "1",
        "-q:v",
        "2",
        str(out_png),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def set_thumbnail(youtube, video_id: str, png_path: Path):
    media = MediaFileUpload(str(png_path), mimetype="image/png")
    log("thumb", f"Setting thumbnail: {png_path.name}", CYAN)
    request = youtube.thumbnails().set(videoId=video_id, media_body=media)
    request.execute()
    log("thumb", "Thumbnail set.", GREEN)


def main():
    p = argparse.ArgumentParser(description="Upload a video to YouTube with OAuth.")
    p.add_argument("--file", required=True, help="Path to the MP4 to upload")
    p.add_argument("--title", help="Video title; defaults to filename stem")
    p.add_argument("--description", default="", help="Video description")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--category-id", default="10", help="YouTube categoryId (default=10 Music)")
    # Default privacy is PRIVATE
    p.add_argument("--privacy", default="private", choices=["public", "unlisted", "private"], help="Privacy status")
    # Default is NOT made for kids unless you explicitly pass this flag
    p.add_argument("--made-for-kids", action="store_true", help="Mark as made for kids")
    p.add_argument("--thumb-from-sec", type=float, default=0.5, help="Timestamp to capture thumbnail frame (sec)")
    p.add_argument("--no-thumbnail", action="store_true", help="Skip setting a thumbnail")

    args = p.parse_args()

    mp4 = Path(args.file).resolve()
    if not mp4.exists():
        print(json.dumps({
            "ok": False,
            "error": "FileNotFound",
            "message": f"File not found: {str(mp4)}",
        }))
        sys.exit(1)

    title = args.title or mp4.stem
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    # OAuth
    creds = get_creds()
    yt = build_youtube(creds)

    # Upload
    vid = upload_video(
        yt,
        mp4,
        title=title,
        description=args.description,
        tags=tags,
        category_id=args.category_id,
        privacy_status=args.privacy,
        made_for_kids=bool(args.made_for_kids),
    )

    # Thumbnail (capture from title card region by default)
    if not args.no_thumbnail:
        thumb_png = mp4.with_suffix(".thumb.png")
        try:
            extract_thumbnail_frame(mp4, thumb_png, args.thumb_from_sec)
            set_thumbnail(yt, vid, thumb_png)
        except subprocess.CalledProcessError:
            log("thumb", "ffmpeg failed to capture thumbnail; skipping.", YELLOW)
        except HttpError as e:
            log("thumb", f"Failed to set thumbnail: {e}", YELLOW)

    print(json.dumps({"ok": True, "video_id": vid, "watch_url": f"https://youtu.be/{vid}"}))


if __name__ == "__main__":
    main()
# end of 5_upload.py
