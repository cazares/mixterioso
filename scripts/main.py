#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path
import re

from .common import IOFlags, Paths, log, slugify
from .offset_tuner import tune_offset
from .step1_fetch import step1_fetch
from .step2_split import step2_split
from .step3_sync import step3_sync

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


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Mixterioso single-entry pipeline")
    p.add_argument("--query", required=True, help='Format: "Artist - Title"')
    p.add_argument("--confirm-offset", action="store_true", help="Interactively confirm lyric offset")
    p.add_argument("--force", "-f", action="store_true", help="Overwrite without prompts")
    p.add_argument("--dry-run", action="store_true", help="No writes (best-effort)")
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
    mix_mode = "full"
    vocals_db = 0.0
    bass_db = 0.0
    drums_db = 0.0
    other_db = 0.0

    step2_split(
        paths,
        slug=slug,
        mix_mode=mix_mode,
        vocals_db=vocals_db,
        bass_db=bass_db,
        drums_db=drums_db,
        other_db=other_db,
        flags=flags,
    )

    # Step 3: sync (build timings CSV from LRC or VTT)
    step3_sync(paths, slug=slug, flags=flags)

    # Default offset rule (locked):
    # - If LRC exists (and appears valid): +1.0s
    # - Otherwise (e.g., VTT): 0.0s
    lrc_path = paths.timings / f"{slug}.lrc"
    offset = 1.0 if lrc_looks_valid(lrc_path) else 0.0

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# end of main.py