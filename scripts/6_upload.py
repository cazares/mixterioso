#!/usr/bin/env python3
# scripts/6_upload.py
#
# Upload a video to YouTube via the official YouTube Data API.
#
# - Requires OAuth client secrets (NOT just an API key).
# - Looks for client secrets at:
#       $YOUTUBE_CLIENT_SECRETS_JSON   (if set; can be file OR directory)
#       ./client_secret.json           (fallback)
# - Stores OAuth tokens in youtube_token.json next to the client secrets file.
#
# CLI:
#   --file          MP4 path (required)
#   --title         Full YouTube title (optional; defaults to filename stem)
#   --description   Description (optional; default "")
#   --tags          Comma-separated tags (optional)
#   --category-id   YouTube categoryId (default "10" = Music)
#   --privacy       public|unlisted|private (default private)
#   --made-for-kids mark video as made for kids (default False)
#   --thumb-from-sec  time (sec) to capture thumbnail frame (default 0.5)
#   --no-thumbnail    skip thumbnail upload
#
# Extra (for master receipts – OPTIONAL):
#   --slug
#   --profile
#   --offset
#
import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import List, Optional
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_LOG = BASE_DIR / "uploaded"

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ---- Google API imports ----
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError as e:
    # Emit machine-readable error so 0_master can surface it nicely
    print(
        json.dumps(
            {
                "ok": False,
                "error": "MissingDependencies",
                "message": (
                    "Missing YouTube upload dependencies. "
                    "Install: google-api-python-client google-auth-oauthlib "
                    "google-auth-httplib2 inside demucs_env."
                ),
                "detail": str(e),
            }
        )
    )
    sys.exit(1)


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_creds() -> "Credentials":
    """
    Load or create OAuth credentials.

    Resolution order:
      1) If YOUTUBE_CLIENT_SECRETS_JSON is set:
         - If it points to a *directory*, use <dir>/client_secret.json
         - If it points to a *file*, use that file
      2) Else, default to ./client_secret.json
    """
    env_val = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON")
    if env_val:
        base_path = Path(env_val).expanduser().resolve()
        if base_path.is_dir():
            secrets_path = base_path / "client_secret.json"
        else:
            secrets_path = base_path
    else:
        secrets_path = (BASE_DIR / "client_secret.json").resolve()

    if not secrets_path.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "MissingOAuthClientSecrets",
                    "message": (
                        "Missing OAuth client secrets JSON. "
                        "Set env YOUTUBE_CLIENT_SECRETS_JSON to your Google OAuth client file "
                        "or to a directory containing client_secret.json, "
                        "or place client_secret.json in the project root. "
                        "API keys (YOUTUBE_API_KEY) are NOT sufficient for uploads."
                    ),
                    "expected_path": str(secrets_path),
                }
            )
        )
        sys.exit(1)

    token_path = secrets_path.with_name("youtube_token.json")
    creds: Optional["Credentials"] = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("auth", "Refreshing OAuth token...", CYAN)
            creds.refresh(Request())
        else:
            log("auth", f"Launching OAuth flow using {secrets_path.name}...", CYAN)
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)

            # Prefer local server flow (opens browser, copies code automatically)
            # Handle older versions that may not accept all kwargs.
            try:
                creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            except TypeError:
                # Older versions might not support prompt/access_type kwargs
                try:
                    creds = flow.run_local_server(port=0)
                except AttributeError as e:
                    # Extremely old version — tell user to upgrade.
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "OAuthFlowUnsupported",
                                "message": (
                                    "Your google-auth-oauthlib version does not support "
                                    "run_local_server(). Please upgrade it inside demucs_env:\n"
                                    "  pip3 install --upgrade google-auth-oauthlib"
                                ),
                                "detail": str(e),
                            }
                        )
                    )
                    sys.exit(1)

        token_path.write_text(creds.to_json(), encoding="utf-8")
        log("auth", f"Stored credentials in {token_path}", GREEN)

    return creds


def build_youtube(creds: "Credentials"):
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
    """
    Upload a single video file. Returns videoId.
    """
    log("upload", f"Uploading: {file_path.name}", CYAN)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags if tags else None,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"\r{pct}% uploaded", end="", flush=True)

    print()  # newline after progress
    if "id" not in response:
        raise RuntimeError(f"Unexpected response from YouTube: {response!r}")

    vid = response["id"]
    log("upload", f"Upload complete. Video ID: {vid}", GREEN)
    return vid


def extract_thumbnail_frame(mp4_path: Path, thumb_png: Path, ts_sec: float) -> None:
    """
    Use ffmpeg to capture a frame as PNG at ts_sec seconds.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(ts_sec),
        "-i",
        str(mp4_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=1280:-1",
        str(thumb_png),
    ]
    log("thumb", " ".join(cmd), BLUE)
    subprocess.run(cmd, check=True)


def set_thumbnail(youtube, video_id: str, thumb_png: Path) -> None:
    media = MediaFileUpload(str(thumb_png), mimetype="image/png")
    request = youtube.thumbnails().set(videoId=video_id, media_body=media)
    request.execute()
    log("thumb", "Thumbnail set.", GREEN)


def write_upload_receipt(
    slug: Optional[str],
    profile: Optional[str],
    offset: Optional[float],
    video_id: str,
    title: str,
) -> None:
    """
    Optional receipt for 0_master. Only writes if slug/profile/offset are all provided.
    """
    if slug is None or profile is None or offset is None:
        return

    UPLOAD_LOG.mkdir(parents=True, exist_ok=True)
    receipt = {
        "slug": slug,
        "profile": profile,
        "offset": offset,
        "video_id": video_id,
        "title": title,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    tag = f"{offset:+.3f}"
    out = UPLOAD_LOG / f"{slug}_{profile}_offset_{tag}.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    log("upload", f"Saved upload receipt to {out}", GREEN)


def parse_args():
    p = argparse.ArgumentParser(description="Upload a video to YouTube with OAuth.")
    p.add_argument("--file", required=True, help="Path to the MP4 to upload")
    p.add_argument("--title", help="Video title; defaults to filename stem")
    p.add_argument("--description", default="", help="Video description")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument(
        "--category-id",
        default="10",
        help="YouTube categoryId (default=10 Music)",
    )
    # Default privacy is PRIVATE
    p.add_argument(
        "--privacy",
        default="private",
        choices=["public", "unlisted", "private"],
        help="Privacy status",
    )
    # Default is NO: not made for kids unless explicitly passed
    p.add_argument(
        "--made-for-kids",
        action="store_true",
        help="Mark as made for kids",
    )
    p.add_argument(
        "--thumb-from-sec",
        type=float,
        default=0.5,
        help="Timestamp to capture thumbnail frame (sec)",
    )
    p.add_argument(
        "--no-thumbnail",
        action="store_true",
        help="Skip setting a thumbnail",
    )

    # Extra args for 0_master receipts (optional)
    p.add_argument("--slug", help="Song slug (for receipt naming)", default=None)
    p.add_argument("--profile", help="Mix profile (for receipt naming)", default=None)
    p.add_argument(
        "--offset",
        type=float,
        help="Offset used for this render (for receipt naming)",
        default=None,
    )

    return p.parse_args()


def main():
    args = parse_args()

    mp4 = Path(args.file).resolve()
    if not mp4.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "FileNotFound",
                    "message": f"File not found: {str(mp4)}",
                }
            )
        )
        sys.exit(1)

    title = args.title or mp4.stem
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    # OAuth + service
    creds = get_creds()
    yt = build_youtube(creds)

    try:
        vid = upload_video(
            yt,
            mp4,
            title=title,
            description=args.description,
            tags=tags,
            category_id=args.category_id,
            privacy_status=args.privacy,
            made_for_kids=args.made_for_kids,
        )
    except HttpError as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "YouTubeUploadError",
                    "message": str(e),
                }
            )
        )
        sys.exit(1)

    # Thumbnail (optional)
    if not args.no_thumbnail:
        thumb_png = mp4.with_suffix(".mp4.thumb.png")
        try:
            extract_thumbnail_frame(mp4, thumb_png, args.thumb_from_sec)
            set_thumbnail(yt, vid, thumb_png)
        except subprocess.CalledProcessError:
            log("thumb", "ffmpeg failed to capture thumbnail; skipping.", YELLOW)
        except HttpError as e:
            log("thumb", f"Failed to set thumbnail: {e}", YELLOW)

    # Receipt for 0_master (if info provided)
    write_upload_receipt(args.slug, args.profile, args.offset, vid, title)

    # Final JSON result
    print(
        json.dumps(
            {"ok": True, "video_id": vid, "watch_url": f"https://youtu.be/{vid}"},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

# end of 6_upload.py
