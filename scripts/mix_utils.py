#!/usr/bin/env python3
"""
Shared mix helpers for 2_stems.py and 0_master.py.
Ensures consistent loading/saving of mix configs and
provides a canonical way to detect stems, load defaults,
and guarantee compatibility with 4-stem Demucs models.

This module intentionally contains *no* ffmpeg logic.
"""

import json
from pathlib import Path

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ----------------------------------------------------------
# CONFIG LOADING / SAVING
# ----------------------------------------------------------

def load_existing_config(slug: str, profile: str):
    """
    Returns (volumes_dict, config_path or None)

    Accepts both:
        mixes/slug_profile.json   ← modern path
        mixes/slug.json           ← legacy compatibility

    Returns (None, path) if file exists but cannot be parsed.
    """
    base = Path(__file__).resolve().parent.parent
    mixes_dir = base / "mixes"
    mixes_dir.mkdir(exist_ok=True)

    new_path = mixes_dir / f"{slug}_{profile}.json"
    old_path = mixes_dir / f"{slug}.json"

    path = None
    if new_path.exists():
        path = new_path
    elif old_path.exists():
        path = old_path

    if not path:
        return None, None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vols = data.get("volumes", {})
        if isinstance(vols, dict):
            return vols, path
        return None, path
    except Exception:
        return None, path


def save_config(slug: str, profile: str, model: str, volumes: dict) -> Path:
    """
    Writes the canonical config file:

        mixes/slug_profile.json

    Always JSON, always includes slug/profile/model/volumes.
    """
    base = Path(__file__).resolve().parent.parent
    mixes_dir = base / "mixes"
    mixes_dir.mkdir(exist_ok=True)

    path = mixes_dir / f"{slug}_{profile}.json"
    payload = {
        "slug": slug,
        "profile": profile,
        "model": model,
        "volumes": volumes,
    }

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log("MIXCFG", f"Saved mix config to {path}", GREEN)
    return path


# ----------------------------------------------------------
# STEM RESOLUTION HELPERS (4-stem only)
# ----------------------------------------------------------

def stem_path_for(track: str, separated_dir: Path) -> Path | None:
    """
    Resolves a mixed track name into a concrete WAV path:

      Preferred 4-stem Demucs files:
          vocals.wav
          bass.wav
          drums.wav
          other.wav

      Fallback behavior:
          guitar → other.wav
          piano  → other.wav

    Returns the Path if found, or None if not found.
    """
    direct = separated_dir / f"{track}.wav"
    if direct.exists():
        return direct

    # fallback rules
    if track in ("guitar", "piano"):
        fb = separated_dir / "other.wav"
        if fb.exists():
            return fb

    # no match
    return None


def validate_stems_for_mix(volumes: dict, separated_dir: Path):
    """
    Ensures that at least ONE valid stem exists for the
    requested volumes dict. This prevents confusing errors
    from downstream render code.

    Raises SystemExit if a requested track cannot be resolved.
    """
    for t in volumes.keys():
        p = stem_path_for(t, separated_dir)
        if p is None:
            raise SystemExit(
                f"Stem not found for track '{t}'. "
                f"Expected {t}.wav or fallback in: {separated_dir}"
            )


# ----------------------------------------------------------
# PROFILE DEFAULTS (shared)
# ----------------------------------------------------------

def profile_defaults(profile: str) -> dict:
    """
    Centralized place to define default linear volumes
    for every supported profile. This keeps 2_stems.py
    and any future code consistent.
    """
    if profile == "karaoke":
        return {
            "vocals": 0.0,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }

    if profile == "car-karaoke":
        return {
            "vocals": 0.35,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }

    if profile == "no-bass":
        return {
            "vocals": 1.0,
            "bass": 0.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }

    if profile == "car-bass-karaoke":
        return {
            "vocals": 0.35,
            "bass": 0.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }

    # Lyrics mode: keep everything 1.0
    return {
        "vocals": 1.0,
        "bass": 1.0,
        "guitar": 1.0,
        "piano": 1.0,
        "other": 1.0,
    }
