#!/usr/bin/env python3
# scripts/6_upload.py
#
# STEP 6: Upload MP4 to YouTube
# - Extract intro-frame thumbnail
# - Upload video
# - Apply generated thumbnail
# - Public by default
# - Always: "NOT made for kids"
# - Output JSON receipt for 0_master.py

import os
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path

# ────────────────────────────────────────────────────────────
# ANSI COLORS
# ────────────────────────────────────────────────────────────
RESET="\033[0m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"

def log(prefix, msg, color=RESET):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{prefix}] {msg}{RESET}")

# ────────────────────────────────────────────────────────────
# YouTube API (simple Google OAuth refresh + upload)
# ────────────────────────────────────────────────────────────

import requests

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status"
YOUTUBE_THUMB_URL  = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}"

def get_access_token():
    """Exchange refresh token → access token."""
    cid     = os.getenv("YOUTUBE_CLIENT_ID")
    csecret = os.getenv("YOUTUBE_CLIENT_SECRET")
    rtoken  = os.getenv("YOUTUBE_REFRESH_TOKEN")

    if not cid or not csecret or not rtoken:
        raise RuntimeError("Missing YouTube OAuth env vars.")

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": cid,
            "client_secret": csecret,
            "refresh_token": rtoken,
            "grant_type": "refresh_token",
        }
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth refresh failed: {resp.text}")

    return resp.json()["access_token"]

def upload_video(video_path, title, description, visibility, made_for_kids):
    access = get_access_token()

    snippet = {
        "title": title,
        "description": description,
        "categoryId": "10",  # Music
    }

    status = {
        "privacyStatus": visibility,
        "selfDeclaredMadeForKids": made_for_kids
    }

    body = {
        "snippet": snippet,
        "status": status
    }

    headers = {
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
    }

    # Init upload
    init = requests.post(YOUTUBE_UPLOAD_URL, headers=headers, data=json.dumps(body))
    if init.status_code not in (200, 201,  resumable_ok := 308):
        raise RuntimeError(f"Init upload failed: {init.text}")

    upload_url = init.headers.get("Location")
    if not upload_url:
        raise RuntimeError("Could not obtain upload URL for resumable upload.")

    # Upload file
    video_data = open(video_path, "rb").read()
    upload_headers = {
        "Authorization": f"Bearer {access}",
        "Content-Length": str(len(video_data)),
        "Content-Type": "video/mp4",
    }
    resp = requests.put(upload_url, headers=upload_headers, data=video_data)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Video upload failed: {resp.text}")

    video_id = resp.json()["id"]
    return video_id, access

def upload_thumbnail(video_id, thumbnail_path, access_token):
    url = YOUTUBE_THUMB_URL.format(vid=video_id)
    with open(thumbnail_path, "rb") as f:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            files={"media": ("thumb.png", f, "image/png")}
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Thumbnail upload failed: {resp.text}")

# ────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--mp4", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--visibility", default="public",
                   choices=("public","unlisted","private"))
    p.add_argument("--base-filename", required=True)
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    video_path = Path(args.mp4)
    if not video_path.exists():
        log("Upload", f"MP4 not found: {video_path}", RED)
        print(json.dumps({"ok": False, "error": "mp4-not-found"}))
        return

    # ────────────────────────────────────────────────────────────
    # 1. EXTRACT THUMBNAIL FROM INTRO SCREEN (ALWAYS t=0.0s)
    # ────────────────────────────────────────────────────────────
    thumb_path = video_path.with_suffix(".thumbnail.png")

    log("THUMB", "Extracting thumbnail (t=0.00s) ...", CYAN)

    cmd = [
        "ffmpeg", "-y",
        "-ss", "0",
        "-i", str(video_path),
        "-vframes", "1",
        str(thumb_path)
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for ln in proc.stdout.split("\n"):
        if ln.strip():
            print(f"{CYAN}[ffmpeg]{RESET} {ln}")
    if proc.returncode != 0:
        log("THUMB", "Thumbnail extraction failed", RED)
        print(json.dumps({"ok": False, "error": "thumbnail-failed"}))
        return

    log("THUMB", f"Thumbnail ready: {thumb_path}", GREEN)

    # ────────────────────────────────────────────────────────────
    # 2. UPLOAD TO YOUTUBE
    # ────────────────────────────────────────────────────────────
    log("Upload", "Starting YouTube upload ...", CYAN)

    try:
        video_id, access = upload_video(
            video_path=str(video_path),
            title=args.title,
            description=args.description,
            visibility=args.visibility,
            made_for_kids=False    # ALWAYS NOT FOR KIDS
        )
    except Exception as e:
        log("Upload", f"Video upload FAILED: {e}", RED)
        print(json.dumps({"ok": False, "error": str(e)}))
        return

    log("Upload", f"Video uploaded: https://youtu.be/{video_id}", GREEN)

    # ────────────────────────────────────────────────────────────
    # 3. UPLOAD THUMBNAIL
    # ────────────────────────────────────────────────────────────
    log("Thumb", "Uploading custom thumbnail ...", CYAN)

    try:
        upload_thumbnail(video_id, thumb_path, access)
    except Exception as e:
        log("Thumb", f"Thumbnail upload FAILED: {e}", RED)
        print(json.dumps({
            "ok": True,
            "warning": "thumb-failed",
            "video_id": video_id,
            "error": str(e)
        }))
        return

    log("Thumb", "Thumbnail applied successfully", GREEN)

    # ────────────────────────────────────────────────────────────
    # Success JSON receipt
    # ────────────────────────────────────────────────────────────
    print(json.dumps({
        "ok": True,
        "video_id": video_id,
        "watch_url": f"https://youtu.be/{video_id}",
        "thumbnail": str(thumb_path),
        "mp4": str(video_path)
    }))

if __name__ == "__main__":
    main()

# end of 6_upload.py
