#!/usr/bin/env python3
"""Step 2: Create the audio mix used for video rendering.

Design goals:
- Default output is "full" (all instruments + vocals) by copying mp3s/<slug>.mp3 to mixes/<slug>.mp3
- Optional stems-based mixing (Demucs) supports per-stem dB adjustments:
    vocals, bass, drums, other

Demucs stems are discovered under:
  separated/**/<slug>/{vocals,bass,drums,other}.wav

If stems are requested and not present, Step2 will attempt to run `demucs` to generate them.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .common import IOFlags, Paths, log, run_cmd, should_write


def _find_stems_dir(paths: Paths, slug: str) -> Optional[Path]:
    # Common Demucs layout: separated/<model>/<slug>/*.wav
    # We'll search a couple patterns to be robust.
    root = paths.separated
    if not root.exists():
        return None

    direct = root / slug
    if (direct / "vocals.wav").exists():
        return direct

    hits = list(root.glob(f"*/{slug}/vocals.wav"))
    if hits:
        return hits[0].parent

    # Last resort: any nested match (could be separated/whatever/<slug>/...)
    hits = list(root.glob(f"**/{slug}/vocals.wav"))
    if hits:
        return hits[0].parent

    return None


def _ensure_stems(paths: Paths, slug: str, mp3_path: Path, *, flags: IOFlags) -> Path:
    stems_dir = _find_stems_dir(paths, slug)
    if stems_dir is not None:
        return stems_dir

    # Need to run demucs
    if flags.dry_run:
        log("SPLIT", f"DRY-RUN: would run demucs for {mp3_path}")
        # Predict the output dir for the common case.
        return paths.separated / "htdemucs" / slug

    if shutil.which("demucs") is None:
        raise RuntimeError("demucs not found on PATH (needed for stems-based mixing)")

    paths.separated.mkdir(parents=True, exist_ok=True)

    # Use a stable model name; users can change later without changing the pipeline contract.
    cmd = [
        "demucs",
        "-n", "htdemucs",
        "-o", str(paths.separated),
        str(mp3_path),
    ]
    rc = run_cmd(cmd, tag="DEMUCS", dry_run=flags.dry_run)
    if rc != 0:
        raise RuntimeError(f"demucs failed with code {rc}")

    stems_dir = _find_stems_dir(paths, slug)
    if stems_dir is None:
        raise RuntimeError(f"demucs completed but stems not found for slug={slug} under {paths.separated}")
    return stems_dir


def _mix_from_stems(
    stems_dir: Path,
    out_wav: Path,
    *,
    vocals_db: float,
    bass_db: float,
    drums_db: float,
    other_db: float,
    flags: IOFlags,
) -> None:
    in_vocals = stems_dir / "vocals.wav"
    in_bass   = stems_dir / "bass.wav"
    in_drums  = stems_dir / "drums.wav"
    in_other  = stems_dir / "other.wav"

    for p in [in_vocals, in_bass, in_drums, in_other]:
        if not p.exists():
            raise RuntimeError(f"Missing stem file: {p}")

    out_wav.parent.mkdir(parents=True, exist_ok=True)

    # Apply per-stem gain in dB, then mix.
    # alimiter reduces clipping risk if boosts push over 0 dBFS.
    fc = (
        f"[0:a]volume={vocals_db}dB[v];"
        f"[1:a]volume={bass_db}dB[b];"
        f"[2:a]volume={drums_db}dB[d];"
        f"[3:a]volume={other_db}dB[o];"
        f"[v][b][d][o]amix=inputs=4:normalize=0,alimiter=limit=0.98[m]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_vocals),
        "-i", str(in_bass),
        "-i", str(in_drums),
        "-i", str(in_other),
        "-filter_complex", fc,
        "-map", "[m]",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    rc = run_cmd(cmd, tag="FFMIX", dry_run=flags.dry_run)
    if rc != 0:
        raise RuntimeError(f"ffmpeg mix failed with code {rc}")


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
) -> str:
    """Returns a short string describing what happened."""

    mp3_path = paths.mp3s / f"{slug}.mp3"
    if not mp3_path.exists():
        raise RuntimeError(f"Missing MP3 for slug={slug} at {mp3_path}")

    paths.mixes.mkdir(parents=True, exist_ok=True)

    if mix_mode == "full":
        out_mp3 = paths.mixes / f"{slug}.mp3"
        if out_mp3.exists() and not should_write(out_mp3, flags, label="mix_mp3"):
            log("MIX", f"Reusing mix: {out_mp3}")
            return "reuse_full"
        if flags.dry_run:
            log("MIX", f"DRY-RUN: would copy {mp3_path} -> {out_mp3}")
            return "dry_full"
        shutil.copy2(mp3_path, out_mp3)
        log("MIX", f"Wrote full mix: {out_mp3}")
        return "full"

    # stems-based modes
    out_wav = paths.mixes / f"{slug}.wav"
    if out_wav.exists() and not should_write(out_wav, flags, label="mix_wav"):
        log("MIX", f"Reusing mix: {out_wav}")
        return "reuse_stems"

    stems_dir = _ensure_stems(paths, slug, mp3_path, flags=flags)

    if mix_mode == "instrumental":
        vocals_db = -120.0

    _mix_from_stems(
        stems_dir,
        out_wav,
        vocals_db=vocals_db,
        bass_db=bass_db,
        drums_db=drums_db,
        other_db=other_db,
        flags=flags,
    )
    log("MIX", f"Wrote stems mix: {out_wav}")
    return mix_mode


# end of step2_split.py
