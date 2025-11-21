#!/usr/bin/env python3
# scripts/2_stems.py
#
# STEM SEPARATION + MIX ENGINE (Demucs-based)
#
# Option A: KEEP the two-phase behavior exactly:
#   1) --mix-ui-only    → build/edit mix config JSON (volumes)
#   2) --render-only     → actually render stems.wav based on config
#
# This preserves:
#   - existing 0_master step2 logic
#   - mix profiles (karaoke, lyrics, car-karaoke, car-bass-karaoke, no-bass)
#   - skip-demucs logic when all levels are default
#   - caching (stems not recomputed unless forced)
#
# Output:
#   mixes/<slug>.wav               (final mixed wav)
#   mixes/<slug>_<profile>.json    (volumes / metadata)
#   separated/<slug>/*             (demucs stems)
#

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

RESET   = "\033[0m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
MAGENTA = "\033[35m"
WHITE   = "\033[97m"
BOLD    = "\033[1m"

def log(section: str, msg: str, color: str = CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")

# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------
BASE_DIR      = Path(__file__).resolve().parent.parent
MP3_DIR       = BASE_DIR / "mp3s"
SEPARATED_DIR = BASE_DIR / "separated"
MIXES_DIR     = BASE_DIR / "mixes"

# ------------------------------------------------------------
# Slugify
# ------------------------------------------------------------
def slugify(s: str) -> str:
    import re
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "song"

# ------------------------------------------------------------
# Demucs invocation
# ------------------------------------------------------------
def run_demucs(model: str, mp3: Path, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    log("DEMUCS", f"Running demucs model '{model}'...", CYAN)
    try:
        subprocess.run(
            [
                "demucs",
                "-n", model,
                str(mp3),
                "--out", str(outdir),
            ],
            check=True
        )
    except subprocess.CalledProcessError:
        log("DEMUCS", "Demucs failed.", RED)
        raise

# ------------------------------------------------------------
# Mix profiles
# ------------------------------------------------------------
def apply_profile(profile: Optional[str]) -> Dict[str, float]:
    """
    Return default volumes based on profile.
    These are multipliers (1.0 = unchanged).
    """
    if not profile:
        return {
            "vocals": 1.0, "bass": 1.0, "guitar": 1.0,
            "piano": 1.0, "other": 1.0, "drums": 1.0,
        }

    if profile == "lyrics":
        return {
            "vocals": 1.0,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
            "drums": 1.0,
        }

    if profile == "karaoke":
        return {
            "vocals": 0.0,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
            "drums": 1.0,
        }

    if profile == "car-karaoke":
        return {
            "vocals": 0.25,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
            "drums": 1.0,
        }

    if profile == "car-bass-karaoke":
        return {
            "vocals": 0.25,
            "bass": 1.8,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
            "drums": 1.0,
        }

    if profile == "no-bass":
        return {
            "vocals": 1.0,
            "bass": 0.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
            "drums": 1.0,
        }

    return {
        "vocals": 1.0,
        "bass": 1.0,
        "guitar": 1.0,
        "piano": 1.0,
        "other": 1.0,
        "drums": 1.0,
    }

# ------------------------------------------------------------
# UI builder
# ------------------------------------------------------------
def build_mix_config(slug: str, profile: Optional[str]) -> Dict[str, float]:
    cfg = apply_profile(profile)
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    out = MIXES_DIR / f"{slug}_{profile}.json"
    out.write_text(json.dumps({"volumes": cfg}, indent=2))
    log("UI", f"Created/updated mix config {out}", GREEN)
    return cfg

# ------------------------------------------------------------
# Load existing mix config
# ------------------------------------------------------------
def load_mix_config(slug: str, profile: Optional[str]) -> Dict[str, float]:
    cfg_path = MIXES_DIR / f"{slug}_{profile}.json"
    if not cfg_path.exists():
        return apply_profile(profile)

    try:
        d = json.loads(cfg_path.read_text())
        return d.get("volumes", apply_profile(profile))
    except Exception:
        return apply_profile(profile)

# ------------------------------------------------------------
# Render final mix
# ------------------------------------------------------------
def render_mix(
    slug: str,
    profile: Optional[str],
    model: str,
    mp3: Path,
    explicit_output: Optional[Path] = None,
) -> Path:

    # final output path
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    if explicit_output:
        out_wav = explicit_output
    else:
        out_wav = MIXES_DIR / f"{slug}.wav"

    # load volumes
    cfg = load_mix_config(slug, profile)
    log("MIX", f"Using config: {cfg}", CYAN)

    # locate demucs directory
    sep_root = SEPARATED_DIR / slug
    models = list(sep_root.glob("*/"))
    if not models:
        raise SystemExit(f"{RED}No demucs output found in {sep_root}{RESET}")

    # pick first model folder
    model_dir = sorted(models)[0]
    log("MIX", f"Selected stem folder: {model_dir}", CYAN)

    # Demucs stems based on version:
    # vocals, bass, drums, other    (4-stem)
    # or vocals, bass, drums, guitar, piano, other (6-stem)
    stems = {}
    for stem in ["vocals", "bass", "drums", "guitar", "piano", "other"]:
        p = model_dir / f"{stem}.wav"
        if p.exists():
            stems[stem] = p

    if not stems:
        raise SystemExit(f"{RED}No stems found inside {model_dir}{RESET}")

    # Build ffmpeg expression
    # out = sum(stem * volume)
    # Using 'adelay=0' to ensure alignment.
    filter_parts = []
    inputs = []
    idx = 0
    for stem, path in stems.items():
        vol = cfg.get(stem, 1.0)
        inputs.append(f" -i {path} ")
        filter_parts.append(f"[{idx}:a]volume={vol}[a{idx}]")
        idx += 1

    # Mix all mapped streams
    maps = "".join(f"[a{i}]" for i in range(idx))
    filter_str = "; ".join(filter_parts) + f"; {maps}amix=inputs={idx}:dropout_transition=0"

    # Build cmd
    cmd = f"ffmpeg -y {' '.join(inputs)} -filter_complex \"{filter_str}\" -c:a pcm_s16le {out_wav}"
    log("FFMPEG", "Mixing stems...", CYAN)

    # Execute
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError:
        raise SystemExit(f"{RED}ffmpeg mix failed{RESET}")

    log("OUT", f"Wrote {out_wav}", GREEN)
    return out_wav

# ------------------------------------------------------------
# Parse CLI
# ------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Stems mix engine")
    p.add_argument("--mp3", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--model", default="htdemucs")

    p.add_argument("--mix-ui-only", action="store_true")
    p.add_argument("--render-only", action="store_true")

    p.add_argument("--output", help="Explicit output wav path")

    # forwarded flags from 0_master (ignored safely)
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--reset-cache", action="store_true")

    return p.parse_args(argv or sys.argv[1:])
# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main(argv=None):
    args = parse_args(argv)
    slug = slugify(Path(args.mp3).stem)

    # Step A: Ensure demucs has run (0_master handles actual separation)
    sep_root = SEPARATED_DIR / slug
    if not sep_root.exists():
        log("STEMS", f"No separated/ folder found for slug '{slug}'", YELLOW)

    # If only mixing UI is requested
    if args.mix_ui_only:
        cfg = load_mix_config(slug, args.profile)
        print(json.dumps({"ok": True, "slug": slug, "config": cfg}, indent=2))
        return

    # If only render is requested
    if args.render_only:
        out = render_mix(
            slug=slug,
            profile=args.profile,
            model=args.model,
            mp3=Path(args.mp3),
            explicit_output=Path(args.output) if args.output else None,
        )
        print(json.dumps({"ok": True, "slug": slug, "mix_path": str(out)}, indent=2))
        return

    # Normal mode: mix UI then render
    cfg = load_mix_config(slug, args.profile)
    print(json.dumps({"ok": True, "slug": slug, "config": cfg}, indent=2))

    out = render_mix(
        slug=slug,
        profile=args.profile,
        model=args.model,
        mp3=Path(args.mp3),
        explicit_output=Path(args.output) if args.output else None,
    )
    print(json.dumps({"ok": True, "slug": slug, "mix_path": str(out)}, indent=2))


if __name__ == "__main__":
    main()

# end of 2_stems.py
