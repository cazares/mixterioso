#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path
import re
import time
import csv

from .common import IOFlags, Paths, log, slugify, YELLOW, WHITE, write_text
from .offset_tuner import tune_offset
from .step1_fetch import step1_fetch
from .step2_split import step2_split
from .step3_sync import step3_sync
from .first_word_time import estimate_first_word_time

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def parse_query(q: str) -> tuple[str, str]:
    """Parse required query format: 'Artist - Title'."""
    if " - " not in q:
        raise ValueError('Query must be in the form "Artist - Title"')
    artist, title = [s.strip() for s in q.split(" - ", 1)]
    if not artist or not title:
        raise ValueError('Query must be in the form "Artist - Title"')
    return artist, title


def lrc_looks_valid(lrc_path: Path) -> bool:
    """Heuristic: at least one timestamp tag like [mm:ss.xx]."""
    if not lrc_path.exists():
        return False
    try:
        txt = lrc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return re.search(r"\[\d{1,2}:\d{2}(?:\.\d{1,2})?\]", txt) is not None


def resolve_renderer(scripts_dir: Path) -> Path:
    """Prefer flat scripts/4_mp4.py; fallback to scripts/mixterioso/4_mp4.py."""
    p1 = scripts_dir / "4_mp4.py"
    if p1.exists():
        return p1
    p2 = scripts_dir / "mixterioso" / "4_mp4.py"
    if p2.exists():
        return p2
    raise RuntimeError(f"Renderer not found. Tried: {p1} and {p2}")





def _read_first_time_secs_from_csv(csv_path: Path) -> float | None:
    """Read the first (earliest) time_secs from a canonical timings CSV."""
    if not csv_path.exists():
        return None
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue
                raw = (row.get("time_secs") or "").strip()
                if not raw:
                    continue
                return float(raw)
    except Exception:
        return None
    return None


def _pick_audio_for_first_word(paths: Paths, slug: str) -> Path | None:
    """Prefer mixes/<slug>.mp3 (pipeline invariant); fall back to mp3s/<slug>.mp3."""
    for p in [
        paths.mixes / f"{slug}.mp3",
        paths.mixes / f"{slug}.wav",
        paths.mp3s / f"{slug}.mp3",
    ]:
        if p.exists():
            return p
    return None


def _maybe_autoshift_offset_from_first_word(paths: Paths, slug: str, flags: IOFlags) -> None:
    """
    If timings and audio exist, compute an approximate first-word time.
    If the first lyric line time is 'off' vs computed time, write timings/<slug>.offset
    as a global shift (applies to all lyric lines at render time).

    Safety:
    - If timings/<slug>.offset already exists: do not overwrite unless --force is used
    - Skip entirely on --dry-run (avoid heavy compute)
    """
    if flags.dry_run:
        log("FIRSTWORD", "[dry-run] Skipping first-word compute", WHITE)
        return

    offset_path = paths.timings / f"{slug}.offset"
    if offset_path.exists() and not flags.force:
        # Respect user-tuned or previously locked offsets
        log("FIRSTWORD", f"Offset exists; skipping auto-shift (use --force to overwrite): {offset_path}", WHITE)
        return

    csv_path = paths.timings / f"{slug}.csv"
    first_line_t = _read_first_time_secs_from_csv(csv_path)
    if first_line_t is None:
        log("FIRSTWORD", f"No timings CSV first-line time found; skipping: {csv_path}", WHITE)
        return

    audio_path = _pick_audio_for_first_word(paths, slug)
    if audio_path is None:
        log("FIRSTWORD", f"No audio found for first-word compute; skipping (expected mixes/ or mp3s/)", WHITE)
        return

    res = estimate_first_word_time(str(audio_path), language=None, verbose=False)
    if res is None:
        log("FIRSTWORD", "No first-word time detected; skipping auto-shift", WHITE)
        return

    computed_t = float(res.first_word_time_secs)
    delta = computed_t - float(first_line_t)

    # Treat small differences as noise (first-word estimate is intentionally rough)
    THRESH = 0.75
    if abs(delta) < THRESH:
        log("FIRSTWORD", f"First line looks OK (csv={first_line_t:.3f}s, first_word={computed_t:.3f}s, delta={delta:+.3f}s). No shift.", WHITE)
        return

    # Write the global offset shift
    log("FIRSTWORD", f"Auto-shifting lyrics (TRUSTING first-word): csv_first_line={first_line_t:.3f}s, first_word={computed_t:.3f}s, delta={delta:+.3f}s -> {offset_path}", WHITE)
    write_text(offset_path, f"{delta:.3f}\n", flags, label="offset_auto")


def read_saved_offset(paths: Paths, slug: str) -> float | None:
    """Read timings/<slug>.offset if it exists and contains a float."""
    p = paths.timings / f"{slug}.offset"
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw:
            return None
        return float(raw)
    except Exception:
        return None

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    t0 = time.perf_counter()
    log('', f"[TIMER] Start")

    p = argparse.ArgumentParser(description="Mixterioso single-entry pipeline")
    p.add_argument("--query", required=True, help='Format: "Artist - Title"')
    p.add_argument("--confirm-offset", action="store_true", help="Interactively confirm lyric offset")
    p.add_argument("--force", "-f", action="store_true", help="Overwrite without prompts")
    p.add_argument("--dry-run", action="store_true", help="No writes (best-effort)")
    p.add_argument("--mix-mode", choices=["full", "stems"], default="full", help="Audio mixing: full copies MP3; stems runs Demucs and mixes stems")
    p.add_argument("--vocals", type=float, default=100.0, help="Vocals level percent (100=unchanged, 0=mute)")
    p.add_argument("--bass", type=float, default=100.0, help="Bass level percent (100=unchanged, 0=mute)")
    p.add_argument("--drums", type=float, default=100.0, help="Drums level percent (100=unchanged, 0=mute)")
    p.add_argument("--other", type=float, default=100.0, help="Other level percent (100=unchanged, 0=mute)")
    args = p.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    renderer = resolve_renderer(scripts_dir)

    flags = IOFlags(force=args.force, confirm=False, dry_run=args.dry_run)

    log("MAIN", f"query={args.query}")

    artist, title = parse_query(args.query)
    slug = slugify(title)

    log("MAIN", f"artist={artist}")
    log("MAIN", f"title={title}")
    log("MAIN", f"slug={slug}")

    paths = Paths.from_scripts_dir(scripts_dir)
    paths.ensure()

    # Step 1: fetch (lyrics + audio + (optional) captions/lrc)
    step1_fetch(
        paths,
        query=args.query,
        artist=artist,
        title=title,
        slug=slug,
        flags=flags,
    )

    # Step 2: split/mix
    # NOTE: step2_split requires explicit mix args. Locked v1.x behavior:
    # default mode "full" copies mp3s/<slug>.mp3 to mixes/<slug>.mp3.
    mix_mode = args.mix_mode
    vocals = args.vocals
    bass = args.bass
    drums = args.drums
    other = args.other

    step2_split(
        paths,
        slug=slug,
        mix_mode=mix_mode,
        vocals=vocals,
        bass=bass,
        drums=drums,
        other=other,
        flags=flags,
    )

    # Step 3: sync (build timings CSV from LRC or VTT)
    sync_source = step3_sync(paths, slug=slug, flags=flags)

    _maybe_autoshift_offset_from_first_word(paths, slug, flags)

    # Default offset rule (locked):
    # - If LRC exists (and appears valid): +1.0s
    # - Otherwise (e.g., VTT): 0.0s
    lrc_path = paths.timings / f"{slug}.lrc"

    # Prefer previously locked offset (timings/<slug>.offset) for both interactive and non-interactive runs.
    saved = read_saved_offset(paths, slug)
    if saved is not None:
        offset = saved
        log("OFFSET", f"Using saved offset: {offset:+.2f}s")
    else:
        # Default offset rule (locked):
        # - If LRC exists (and appears valid): +1.0s
        # - Otherwise (e.g., VTT): 0.0s
        offset = 0.0

    if args.confirm_offset:
        offset = tune_offset(
            slug=slug,
            base_offset=offset,
            mixes_dir=paths.mixes,
            timings_dir=paths.timings,
            renderer_path=renderer,
        )

    # Step 4: render (reuse 4_mp4.py unchanged)
    render_cmd = [
        sys.executable,
        str(renderer),
        "--slug",
        slug,
        "--offset",
        str(offset),
    ]
    log("RENDER", " ".join(render_cmd))
    subprocess.run(render_cmd, check=True)

    t1 = time.perf_counter()
    elapsed = t1 - t0
    log('', f"[TIMER] End")
    log('', f"[TIMER] Elapsed: {elapsed:.3f}s ({elapsed/60.0:.2f}m)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# end of main.py
