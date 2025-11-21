#!/usr/bin/env python3
# scripts/5_upload.py
#
# Upload a video to YouTube via the official YouTube Data API.
#
# (patched: dynamic title builder + pass-through args + enhanced receipts)

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
META_DIR = BASE_DIR / "meta"
MIXES_DIR = BASE_DIR / "mixes"

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

DEFAULT_DESCRIPTION = (
    "This video was generated automatically by Mixterioso, an advanced audio mixer and karaoke engine "
    "engineered by 𝗠𝗶𝗴𝘂𝗲𝗹 𝗖𝗮𝘇𝗮𝗿𝗲𝘀. It separates vocals, bass, guitar, and drums, remixes levels, and "
    "produces fully timed on-screen lyrics for karaoke, sing-along, backing tracks, and musician practice.\n"
    "Learn more about the creator, Miguel Cazares, at 🔗 https://miguelengineer.com"
)

def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ---- Helper: load metadata (artist/title) ----
def load_meta(slug: Optional[str]) -> tuple[str, str]:
    """
    Returns (artist, title). Falls back to slug if missing.
    """
    if not slug:
        return "", ""

    p = META_DIR / f"{slug}.json"
    if not p.exists():
        pretty = slug.replace("_", " ")
        return "", pretty

    try:
        d = json.loads(p.read_text())
        artist = (d.get("artist") or "").strip()
        title = (d.get("title") or slug.replace("_", " ")).strip()
        return artist, title
    except Exception:
        pretty = slug.replace("_", " ")
        return "", pretty


# ---- Helper: load mix config (volumes) ----
def load_mix_config(slug: Optional[str], profile: Optional[str]) -> dict:
    """
    Loads volumes from mixes/<slug>_<profile>.json
    Returns {} if not present.
    """
    if not slug or not profile:
        return {}

    p = MIXES_DIR / f"{slug}_{profile}.json"
    if not p.exists():
        return {}

    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and "volumes" in d:
            return d["volumes"]
        return {}
    except Exception:
        return {}


# ---- Helper: classify title according to rules ----
def build_dynamic_title(artist: str,
                        song_title: str,
                        volumes: dict) -> tuple[str, dict]:
    """
    Returns (final_title, debug_info)

    debug_info holds:
      {
        "vocals_pct": ...,
        "instrument": ...,
        "instrument_pct": ...,
        "classification": "...",
      }
    """

    def pct(x):
        try:
            return int(round(float(x) * 100))
        except Exception:
            return 0

    v_voc = pct(volumes.get("vocals", 1.0))
    v_bass = pct(volumes.get("bass", 1.0))
    v_gtr = pct(volumes.get("guitar", 1.0))
    v_pno = pct(volumes.get("piano", 1.0))
    v_oth = pct(volumes.get("other", 1.0))

    # Determine primary instrument changed (if any)
    instrument = None
    instrument_pct = None

    candidates = {
        "Bass": v_bass,
        "Guitar": v_gtr,
        "Piano": v_pno,
        "Other": v_oth,
    }
    changed = [(k, v) for k, v in candidates.items() if v != 100]

    # Rule classification
    classification = ""

    if v_voc == 0 and len(changed) == 0:
        # Pure karaoke
        classification = "karaoke"
        suffix = "Karaoke"

    elif 0 < v_voc < 100 and len(changed) == 0:
        # Car Karaoke variant
        classification = "car-karaoke"
        suffix = f"Car Karaoke, {v_voc}% Vocals"

    elif v_voc == 100 and all(v == 100 for k, v in candidates.items()):
        # Pure lyrics version
        classification = "lyrics"
        suffix = "Karaoke-Style Lyrics"

    elif 0 < v_voc <= 100 and len(changed) == 1:
        # Single instrument modified
        classification = "vocals+instrument"
        (instrument, instrument_pct) = changed[0]
        suffix = f"{v_voc}% Vocals), {instrument_pct}% {instrument} + Karaoke-Style Lyrics"
        suffix = f"({suffix}"  # open-paren moved before X% Vocals

    else:
        # Everything else → fallback
        classification = "fallback-lyrics"
        suffix = "Karaoke-Style Lyrics"

    # Compose final
    artist_part = f"{artist} - " if artist else ""
    final_title = f"{artist_part}{song_title} ({suffix})"

    debug = {
        "vocals_pct": v_voc,
        "instrument": instrument,
        "instrument_pct": instrument_pct,
        "classification": classification,
    }
    return final_title, debug


# ---- Google API imports ----
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError as e:
    print(
        json.dumps(
            {
                "ok": False,
                "error": "MissingDependencies",
                "message": (
                    "Missing YouTube upload dependencies. Install: "
                    "google-api-python-client google-auth-oauthlib google-auth-httplib2"
                ),
                "detail": str(e),
            }
        )
    )
    sys.exit(1)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_creds() -> Credentials:
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
                        "Set env YOUTUBE_CLIENT_SECRETS_JSON or place client_secret.json in project root."
                    ),
                    "expected_path": str(secrets_path),
                }
            )
        )
        sys.exit(1)

    token_path = secrets_path.with_name("youtube_token.json")
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("auth", "Refreshing OAuth token...", CYAN)
            creds.refresh(Request())
        else:
            log("auth", f"Launching OAuth flow using {secrets_path.name}...", CYAN)
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
            try:
                creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            except TypeError:
                try:
                    creds = flow.run_local_server(port=0)
                except AttributeError as e:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "OAuthFlowUnsupported",
                                "message": (
                                    "Your google-auth-oauthlib version is too old. "
                                    "Upgrade via: pip3 install --upgrade google-auth-oauthlib"
                                ),
                                "detail": str(e),
                            }
                        )
                    )
                    sys.exit(1)

        token_path.write_text(creds.to_json(), encoding="utf-8")
        log("auth", f"Stored credentials in {token_path}", GREEN)

    return creds


def build_youtube(creds: Credentials):
    return build("youtube", "v3", credentials=creds)


def parse_args():
    # MINIMAL DIFF: use parse_known_args to allow pass-through flags
    p = argparse.ArgumentParser(description="Upload a video to YouTube with OAuth.", add_help=True)

    p.add_argument("--file", required=True, help="Path to the MP4 to upload")
    p.add_argument("--title", help="Video title; overrides auto-title")
    p.add_argument("--description", default="", help="Video description")
    p.add_argument("--tags", default="", help="Comma-separated tags")

    p.add_argument("--category-id", default="10", help="YouTube categoryId (default=10 Music)")

    p.add_argument(
        "--privacy",
        default="private",
        choices=["public", "unlisted", "private"],
        help="Privacy status",
    )

    p.add_argument("--made-for-kids", action="store_true")

    p.add_argument(
        "--thumb-from-sec",
        type=float,
        default=0.5,
        help="Timestamp to capture thumbnail frame (sec)",
    )
    p.add_argument("--no-thumbnail", action="store_true")

    # Extra metadata for receipts
    p.add_argument("--slug", default=None)
    p.add_argument("--profile", default=None)
    p.add_argument("--offset", type=float, default=None)

    # Video description on the YouTube video itself (persistent after upload)
    p.add_argument("--description", default=DEFAULT_DESCRIPTION, help="Video description")

    args, unknown = p.parse_known_args()
    args._unknown = unknown  # store unknown flags minimally
    return args


def main():
    args = parse_args()

    mp4 = Path(args.file).resolve()
    if not mp4.exists():
        print(json.dumps({"ok": False, "error": "FileNotFound", "message": str(mp4)}))
        sys.exit(1)

    # Load meta + volumes
    artist, song_title = load_meta(args.slug)
    volumes = load_mix_config(args.slug, args.profile)

    # Build dynamic title only if user did not override --title
    if args.title:
        final_title = args.title
        title_debug = {}
    else:
        final_title, title_debug = build_dynamic_title(artist, song_title, volumes)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    # OAuth
    creds = get_creds()
    yt = build_youtube(creds)

    try:
        video_id = upload_video(
            yt,
            mp4,
            title=final_title,
            description=args.description,
            tags=tags,
            category_id=args.category_id,
            privacy_status=args.privacy,
            made_for_kids=args.made_for_kids,
        )
    except HttpError as e:
        print(json.dumps({"ok": False, "error": "YouTubeUploadError", "message": str(e)}))
        sys.exit(1)

    # Thumbnail
    if not args.no_thumbnail:
        thumb_png = mp4.with_suffix(".mp4.thumb.png")
        try:
            extract_thumbnail_frame(mp4, thumb_png, args.thumb_from_sec)
            set_thumbnail(yt, video_id, thumb_png)
        except subprocess.CalledProcessError:
            log("thumb", "ffmpeg failed to capture thumbnail; skipping.", YELLOW)
        except HttpError as e:
            log("thumb", f"Failed to set thumbnail: {e}", YELLOW)

    # Enhanced receipt (patch)
    write_upload_receipt(
        slug=args.slug,
        profile=args.profile,
        offset=args.offset,
        video_id=video_id,
        title=final_title,
        volumes=volumes,
        debug=title_debug,
    )

    print(json.dumps({"ok": True, "video_id": video_id, "watch_url": f"https://youtu.be/{video_id}"},
                     indent=2))
def write_upload_receipt(
    slug: Optional[str],
    profile: Optional[str],
    offset: Optional[float],
    video_id: str,
    title: str,
    volumes: dict,
    debug: dict,
) -> None:
    """
    Enhanced receipt:
      - slug
      - profile
      - offset
      - video_id
      - final title
      - volumes used
      - title classification debugging
    Only writes when slug/profile/offset exist.
    """
    if slug is None or profile is None or offset is None:
        return

    UPLOAD_LOG.mkdir(parents=True, exist_ok=True)
    tag = f"{offset:+.3f}"

    out_path = UPLOAD_LOG / f"{slug}_{profile}_offset_{tag}.json"
    payload = {
        "slug": slug,
        "profile": profile,
        "offset": offset,
        "video_id": video_id,
        "title": title,
        "volumes": volumes,
        "title_debug": debug,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log("upload", f"Saved upload receipt to {out_path}", GREEN)


# END OF FILE
if __name__ == "__main__":
    main()
