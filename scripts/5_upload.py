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
