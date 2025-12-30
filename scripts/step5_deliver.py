#!/usr/bin/env python3
"""Step 5: Deliver (package outputs).

Creates a single zip that contains the most useful artifacts for a given slug.

Inputs (best-effort):
- output/<slug>.mp4
- timings/<slug>.csv
- timings/<slug>.lrc (if present)
- timings/<slug>*.vtt (if present)
- timings/<slug>.offset (if present)
- txts/<slug>.txt (if present)
- mixes/<slug>.(wav|mp3...) (if present)
- meta/<slug>*.json (if present)

Output:
- output/<slug>_deliver.zip
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Iterable, List

from .common import IOFlags, Paths, log, should_write, slugify, GREEN, YELLOW, BLUE


def _iter_candidates(paths: Paths, slug: str) -> List[Path]:
    out: List[Path] = []

    # Primary output
    out.append(paths.output / f"{slug}.mp4")
    out.append(paths.output / f"{slug}.ass")

    # Core timing inputs
    out.append(paths.timings / f"{slug}.csv")
    out.append(paths.timings / f"{slug}.lrc")
    out.append(paths.timings / f"{slug}.offset")

    # VTT fallbacks (language-tagged, etc.)
    out.extend(sorted(paths.timings.glob(f"{slug}*.vtt")))

    # Lyrics text
    out.append(paths.txts / f"{slug}.txt")

    # Audio (best-effort)
    out.extend(sorted(paths.mixes.glob(f"{slug}.*")))

    # Step meta summaries
    out.extend(sorted(paths.meta.glob(f"{slug}*.json")))

    # De-dup while preserving order
    seen: set[Path] = set()
    dedup: List[Path] = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        dedup.append(p)
    return dedup


def _rel_arcname(paths: Paths, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(paths.root.resolve()))
    except Exception:
        return p.name


def step5_deliver(paths: Paths, *, slug: str, flags: IOFlags) -> Path:
    slug = slugify(slug)
    zip_path = paths.output / f"{slug}_deliver.zip"

    # Safe-by-default reuse
    if zip_path.exists() and not should_write(zip_path, flags, label="deliver"):
        log("DELIVER", f"Reusing existing: {zip_path}", YELLOW)
        return zip_path

    candidates = _iter_candidates(paths, slug)
    existing = [p for p in candidates if p.exists()]

    if not (paths.output / f"{slug}.mp4").exists():
        raise FileNotFoundError(f"Missing MP4 to deliver: {paths.output / f'{slug}.mp4'}")

    if flags.dry_run:
        log("DELIVER", f"[dry-run] Would write {zip_path}", BLUE)
        for p in existing:
            log("DELIVER", f"[dry-run] include {p}", BLUE)
        return zip_path

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in existing:
            z.write(p, arcname=_rel_arcname(paths, p))

    log("DELIVER", f"Wrote {zip_path} ({len(existing)} files)", GREEN)
    return zip_path


def parse_args(argv: List[str] | None = None):
    p = argparse.ArgumentParser(description="Package Mixterioso outputs into a deliverable zip")
    p.add_argument("--slug", required=True, help="Song slug")
    p.add_argument("--force", "-f", action="store_true", help="Overwrite existing deliver zip")
    p.add_argument("--dry-run", action="store_true", help="No writes")
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    scripts_dir = Path(__file__).resolve().parent
    paths = Paths.from_scripts_dir(scripts_dir)
    paths.ensure()
    flags = IOFlags(force=args.force, confirm=False, dry_run=args.dry_run)
    step5_deliver(paths, slug=args.slug, flags=flags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# end of step5_deliver.py
