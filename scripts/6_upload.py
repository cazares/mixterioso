#!/usr/bin/env python3
# scripts/6_upload.py
#
# STEP 6: Upload final MP4 to YouTube (formerly 5_upload.py)
# ----------------------------------------------------------
# - Uses OAuth (client_secret.json)
# - Colorized streaming logs
# - Prompts user about PUBLIC visibility ("are you SURE?")
# - Can auto-select thumbnail from MP4 frame
# - Final output is ALWAYS JSON for 0_master.py
# - Filename passed in is FINAL MP4 (slug.mp4)
#
# GENTLE RULES:
# - NEVER assume YOUTUBE_API_KEY is enough (it's NOT)
# - MUST have OAuth JSON (client_secret.json)
# - Tokens stored in same directory
# - Works on macOS, Codespaces (if Chrome allowed), MacInCloud
#
# CLI:
#   --file path/to/final.mp4   (required)
#   --title "..."              (optional)
#   --description "..."        (optional)
#   --tags "a,b,c"             (optional)
#   --public                   ask confirmation, then upload public
#   --unlisted                 upload unlisted
#   --thumb-sec X              take screenshot at X seconds
#   --no-thumbnail
#   --slug, --offset           (optional for master receipts)
#
# Notes:
#   The pipeline ALWAYS sets madeForKids = false.

from __future__ import annotations
import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

# ANSI colors
RESET = "\033[0m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
RED   = "\033[31m"
BLUE  = "\033[34m"

BASE = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE / "output"
UPLOAD_LOG = BASE / "uploaded"

T_THUMB = 0.041

def log(section, msg, color=CYAN):
    print(f"{color}[{section}]{RESET} {msg}")

# Google API imports ----------------------------------------------------------
try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except Exception as e:
    print(json.dumps({
        "ok": False,
        "error": "missing-deps",
        "detail": str(e)
    }))
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


# -----------------------------------------------------------------------------
# Credential Loader
# -----------------------------------------------------------------------------
def get_creds() -> Credentials:
    env_p = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON")
    if env_p:
        s = Path(env_p).expanduser().resolve()
        secrets = s / "client_secret.json" if s.is_dir() else s
    else:
        secrets = (BASE / "client_secret.json").resolve()

    if not secrets.exists():
        print(json.dumps({
            "ok": False,
            "error": "missing-oauth-secret",
            "message": "client_secret.json not found",
            "path": str(secrets)
        }))
        sys.exit(1)

    token = secrets.with_name("youtube_token.json")
    creds = None

    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("auth","Refreshing token…",CYAN)
            creds.refresh(Request())
        else:
            log("auth",f"Launching OAuth flow using {secrets.name}",CYAN)
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
                creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            except Exception as e:
                print(json.dumps({
                    "ok": False,
                    "error": "oauth-failed",
                    "detail": str(e)
                }))
                sys.exit(1)

        token.write_text(creds.to_json(), encoding="utf-8")
        log("auth",f"Stored new token at {token}",GREEN)

    return creds


# -----------------------------------------------------------------------------
def extract_thumbnail(mp4: Path, png: Path, t: float):
    cmd = [
        "ffmpeg","-y",
        "-ss", str(t),
        "-i", str(mp4),
        "-frames:v","1",
        "-vf","scale=1280:-1",
        str(png)
    ]
    log("thumb"," ".join(cmd),BLUE)
    subprocess.run(cmd, check=True)


def set_thumb(youtube, video_id: str, png: Path):
    media = MediaFileUpload(str(png), mimetype="image/png")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    log("thumb","Thumbnail updated",GREEN)


# -----------------------------------------------------------------------------
def upload_video(
    youtube,
    mp4: Path,
    title: str,
    desc: str,
    tags: list[str],
    privacy: str,
    made_for_kids: bool,
    category: str = "10",
):
    log("upload",f"Uploading {mp4.name}",CYAN)

    body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": tags or None,
            "categoryId": category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }

    media = MediaFileUpload(str(mp4), chunksize=-1, resumable=True)
    req = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            pct = int(status.progress()*100)
            print(f"{CYAN}[upload]{RESET} {pct}%")

    if "id" not in resp:
        raise RuntimeError("Upload succeeded but no video ID returned.")

    vid = resp["id"]
    log("upload",f"UPLOAD OK → Video ID: {vid}",GREEN)
    return vid


# -----------------------------------------------------------------------------
def write_receipt(slug, offset, video_id, title):
    if slug is None or offset is None:
        return
    UPLOAD_LOG.mkdir(exist_ok=True)
    r = {
        "slug": slug,
        "offset": offset,
        "video_id": video_id,
        "title": title,
        "timestamp": datetime.utcnow().isoformat()+"Z"
    }
    out = UPLOAD_LOG / f"{slug}_upload_receipt.json"
    out.write_text(json.dumps(r, indent=2), encoding="utf-8")
    log("upload",f"Receipt → {out}",GREEN)


# -----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--title")
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--public", action="store_true")
    ap.add_argument("--unlisted", action="store_true")
    ap.add_argument("--thumb-sec", type=float, default=0.5)
    ap.add_argument("--no-thumbnail", action="store_true")

    # master metadata (optional)
    ap.add_argument("--slug")
    ap.add_argument("--offset", type=float)

    ap.add_argument("passthrough", nargs="*")
    args = ap.parse_args()

    # ------------------------------
    # Validate file
    # ------------------------------
    mp4 = Path(args.file).resolve()
    if not mp4.exists():
        print(json.dumps({"ok":False,"error":"file-not-found","path":str(mp4)}))
        sys.exit(1)

    title = args.title or mp4.stem
    tags  = [t.strip() for t in args.tags.split(",") if t.strip()]

    # ------------------------------
    # Determine privacy
    # ------------------------------
    privacy = "private"
    if args.unlisted:
        privacy = "unlisted"
    if args.public:
        # ask confirmation
        ans = input("\nMake video PUBLIC? y/N: ").strip().lower()
        if ans == "y":
            ans2 = input("ARE YOU SURE? This will publish immediately. y/N: ").strip().lower()
            if ans2 == "y":
                privacy = "public"
            else:
                log("upload","Upload remains private",YELLOW)
        else:
            log("upload","Upload remains private",YELLOW)

    # ------------------------------
    # OAuth
    # ------------------------------
    creds = get_creds()
    yt = build("youtube","v3",credentials=creds)

    # ------------------------------
    # UPLOAD
    # ------------------------------
    try:
        vid = upload_video(
            yt,
            mp4,
            title=title,
            desc=args.description,
            tags=tags,
            privacy=privacy,
            made_for_kids=False  # ALWAYS false
        )
    except HttpError as e:
        print(json.dumps({"ok":False,"error":"youtube-error","detail":str(e)}))
        sys.exit(1)

    # ------------------------------
    # Thumbnail
    # ------------------------------
    if not args.no_thumbnail:
        png = mp4.with_suffix(".thumb.png")
        try:
            extract_thumbnail(mp4, png, T_THUMB)
            set_thumb(yt, vid, png)
        except Exception as e:
            log("thumb",f"Thumbnail failed: {e}",YELLOW)

    # ------------------------------
    # Receipt
    # ------------------------------
    write_receipt(args.slug, args.offset, vid, title)

    # ------------------------------
    # JSON Result
    # ------------------------------
    print(json.dumps({
        "ok": True,
        "video_id": vid,
        "watch_url": f"https://youtu.be/{vid}",
        "privacy": privacy,
        "file": str(mp4)
    }, indent=2))


if __name__ == "__main__":
    main()
