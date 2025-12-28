#!/usr/bin/env python3
"""
Step 2 — split / mix audio

Current locked behavior (v1.x):
- No Demucs splitting unless explicitly re-enabled later
- Always ensure mixes/<slug>.mp3 exists
- Renderer (4_mp4.py) must never look at mp3s/
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .common import IOFlags, Paths, log, GREEN, YELLOW


def step2_split(
    paths: Paths,
    *,
    slug: str,
    mix_mode: str,
    vocals_db: float,
    bass_db: float,
    drums_db: float,
    other_db: float,
    flags: IOFlags,
) -> None:
    """
    Guarantee that mixes/<slug>.mp3 exists.

    mix_mode semantics (current):
    - "full": copy mp3s/<slug>.mp3 → mixes/<slug>.mp3
    - "instrumental": same for now (Demucs removed vocals earlier or later)
    - Any mode still guarantees a mix output
    """

    src_mp3 = paths.mp3s / f"{slug}.mp3"
    out_mp3 = paths.mixes / f"{slug}.mp3"

    log("SPLIT", f"Mode={mix_mode}", GREEN)

    if not src_mp3.exists():
        raise FileNotFoundError(f"Source MP3 not found: {src_mp3}")

    # Ensure output directory exists
    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    # Skip copy if already present and not forcing
    if out_mp3.exists() and not flags.force:
        log("SPLIT", f"Using existing mix: {out_mp3}", GREEN)
        return

    # Copy source MP3 as the mix (locked v1 behavior)
    shutil.copy2(src_mp3, out_mp3)
    log("SPLIT", f"Copied full mix to {out_mp3}", GREEN)

    # Final invariant check (defensive)
    if not out_mp3.exists():
        raise RuntimeError(f"Failed to produce mix output: {out_mp3}")

    log("SPLIT", "Step 2 complete (mix guaranteed)", GREEN)


# end of step2_split.py
