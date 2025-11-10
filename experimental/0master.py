#!/usr/bin/env python3
"""
0master.py – Orchestrate the karaoke pipeline.

Steps:
 1: 1download.py   (takes URL/query, derives slug, downloads assets)
 2: 2mix.py        (takes slug)
 3: 3time.py       (takes slug → we call it with --txt/--audio/--timings)
 4: 4calibrate.py  (takes slug)
 5: 5gen_mp4.py    (takes slug)
"""

import sys
import argparse
import csv
import subprocess
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

ABORT_CODE = 99

# Base paths
BASE_DIR = Path(__file__).resolve().parent          # e.g. project/experimental
PROJECT_ROOT = BASE_DIR.parent                      # e.g. project/

# script names (must exist next to this file)
SCRIPT_MAP = {
    1: "1download.py",
    2: "2mix.py",
    3: "3time.py",
    4: "4calibrate.py",
    5: "5gen_mp4.py",
}

# output dirs anchored to project root
MP3_DIR = PROJECT_ROOT / "mp3s"
TXT_DIR = PROJECT_ROOT / "txts"
STEMS_DIR = PROJECT_ROOT / "stems"
TIMING_DIR = PROJECT_ROOT / "timings"
OFFSET_DIR = PROJECT_ROOT / "offsets"
MP4_DIR = PROJECT_ROOT / "mp4s"


def infer_slug_from_input(input_str: str | None) -> str | None:
    """
    Best-effort slug inference.

    - If the user passed a YouTube URL, we don't actually parse it; we rely on
      1download.py to decide the slug, and we use detect_slug_from_disk later.
    - If it looks like a bare slug (no spaces, no 'http'), we return it.
    - Otherwise, None for now.
    """
    if not input_str:
        return None

    s = input_str.strip()
    if not s:
        return None

    lower = s.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return None

    if " " not in s and "/" not in s:
        return s

    return None


def detect_slug_from_disk() -> str | None:
    """
    Try to guess a slug based on files in mp3s/txts, favoring latest modified.
    """
    candidates = set()

    if MP3_DIR.exists():
        for mp3 in MP3_DIR.glob("*.mp3"):
            candidates.add(mp3.stem)

    if TXT_DIR.exists():
        for txt in TXT_DIR.glob("*.txt"):
            candidates.add(txt.stem)

    if not candidates:
        return None

    if len(candidates) == 1:
        return next(iter(candidates))

    if MP3_DIR.exists():
        mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        if mp3s:
            return mp3s[-1].stem

    return None


def step_done(step: int, slug: str | None) -> bool:
    """Check if a given step appears to be completed based on expected files."""
    if not slug:
        return False

    if step == 1:
        mp3 = MP3_DIR / f"{slug}.mp3"
        txt = TXT_DIR / f"{slug}.txt"
        return mp3.exists() and txt.exists()
    elif step == 2:
        vocals = STEMS_DIR / slug / "vocals.wav"
        marker = PROJECT_ROOT / "mix_done" / f"{slug}.done"
        return vocals.exists() or marker.exists()
    elif step == 3:
        timing = TIMING_DIR / f"{slug}.csv"
        return timing.exists()
    elif step == 4:
        offset = OFFSET_DIR / f"{slug}.json"
        return offset.exists()
    elif step == 5:
        mp4 = MP4_DIR / f"{slug}.mp4"
        return mp4.exists()
    return False


def timings_has_rows(slug: str | None) -> bool:
    """Return True if timings/<slug>.csv exists and has at least one data row."""
    if not slug:
        return False
    timing_path = TIMING_DIR / f"{slug}.csv"
    if not timing_path.exists():
        return False
    try:
        with timing_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            # Skip header
            try:
                next(reader)
            except StopIteration:
                return False
            # Check if there is at least one data row
            for _ in reader:
                return True
    except Exception:
        return False
    return False


def run_step(step: int, arg: str | None) -> int:
    """
    Run the script for a given step.

    - Step 1: arg is query/URL (positional).
    - Step 2,4,5: arg is slug (positional).
    - Step 3: arg is slug, but we call 3time.py with --txt/--audio/--timings.
    """
    script_name = SCRIPT_MAP.get(step)
    if not script_name:
        console.print(f"[red]Unknown step {step}[/red]")
        return 1

    script_path = BASE_DIR / script_name
    if not script_path.exists():
        console.print(f"[red]Script not found:[/] {script_path}")
        return 1

    if step == 3 and arg:
        slug = arg.strip()
        txt_path = TXT_DIR / f"{slug}.txt"
        audio_path = MP3_DIR / f"{slug}.mp3"
        timings_path = TIMING_DIR / f"{slug}.csv"

        if not txt_path.exists():
            console.print(f"[red]Missing lyrics txt:[/] {txt_path}")
            return 1
        if not audio_path.exists():
            console.print(f"[red]Missing audio mp3:[/] {audio_path}")
            return 1

        TIMING_DIR.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            str(script_path),
            "--txt",
            str(txt_path),
            "--audio",
            str(audio_path),
            "--timings",
            str(timings_path),
        ]
    else:
        cmd = [sys.executable, str(script_path)]
        if arg:
            cmd.append(arg)

    console.print(f"[cyan]→ Running step {step}: {script_name}[/cyan]")
    try:
        result = subprocess.run(cmd)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return ABORT_CODE
    return result.returncode


def auto_pipeline(url: str) -> None:
    """Run steps 1–5 in order, never skipping based on existing files."""
    slug = infer_slug_from_input(url)
    console.print(
        f"[bold]Auto mode:[/] query/URL=[cyan]{url}[/cyan], initial slug=[magenta]{slug}[/magenta]"
    )

    for step in range(1, 6):
        arg = url if step == 1 else slug
        code = run_step(step, arg)
        if code == ABORT_CODE:
            console.print("[yellow]Pipeline aborted by user.[/yellow]")
            return
        if code != 0:
            if step == 3 and timings_has_rows(slug):
                console.print(
                    "[yellow]Step 3 returned non-zero but timings CSV exists; continuing with saved timings.[/yellow]"
                )
            else:
                console.print(
                    f"[red]Step {step} failed with code {code}. Stopping.[/red]"
                )
                sys.exit(code)
        else:
            console.print(f"[green]Step {step} OK.[/green]")

        if step == 1 and code == 0:
            detected = detect_slug_from_disk()
            if detected:
                console.print(
                    f"[cyan]Detected slug from assets: [magenta]{detected}[/magenta][/cyan]"
                )
                slug = detected

    console.print("[bold green]All steps completed. Final MP4 should be ready.[/bold green]")


def auto_pipeline_existing_slug(slug: str) -> None:
    """
    Auto-mode variant for "reuse previous slug":

    Runs steps 2–5, never calls 1download.py, never skips based on existing files.
    """
    console.print(
        f"[bold]Auto mode (existing slug):[/] slug=[magenta]{slug}[/magenta]"
    )
    for step in range(2, 6):
        arg = slug
        code = run_step(step, arg)
        if code == ABORT_CODE:
            console.print("[yellow]Pipeline aborted by user.[/yellow]")
            return
        if code != 0:
            if step == 3 and timings_has_rows(slug):
                console.print(
                    "[yellow]Step 3 returned non-zero but timings CSV exists; continuing with saved timings.[/yellow]"
                )
                continue
            console.print(
                f"[red]Step {step} failed with code {code}. Stopping.[/red]"
            )
            sys.exit(code)
        console.print(f"[green]Step {step} OK.[/green]")

    console.print("[bold green]Steps 2–5 completed for existing slug.[/bold green]")


def manual_menu(url_or_slug: str | None) -> None:
    """Interactive menu mode with status for each step."""
    url = url_or_slug
    slug = infer_slug_from_input(url) if url else None

    console.print()
    if slug:
        console.print(
            f"[cyan]Manual mode using slug: [magenta]{slug}[/magenta][/cyan]"
        )
    else:
        console.print("[cyan]Manual mode with no slug yet.[/cyan]")

    while True:
        status_map = {s: ("DONE" if step_done(s, slug) else "PENDING") for s in range(1, 6)}

        table = Table(title="Karaoke Pipeline Menu")
        table.add_column("Step", justify="right")
        table.add_column("Script")
        table.add_column("Status")
        table.add_column("Description")

        table.add_row(
            "1", SCRIPT_MAP[1], status_map[1],
            "Download audio + metadata + lyrics (needs query / YT URL)",
        )
        table.add_row(
            "2", SCRIPT_MAP[2], status_map[2],
            "Audio mix / Demucs (needs slug)",
        )
        table.add_row(
            "3", SCRIPT_MAP[3], status_map[3],
            "Manual lyric timing (curses → timings CSV)",
        )
        table.add_row(
            "4", SCRIPT_MAP[4], status_map[4],
            "Calibration (offset JSON)",
        )
        table.add_row(
            "5", SCRIPT_MAP[5], status_map[5],
            "Final MP4 generation",
        )

        console.print()
        console.print(table)
        console.print(
            "[bold white]"
            "Select step to run "
            "(1–5), or 'a' for auto 1→5, 'r' to infer slug, "
            "'q' to quit:[/] ",
            end="",
        )
        choice = sys.stdin.readline().strip().lower()

        if choice == "q":
            console.print("[yellow]Exiting.[/yellow]")
            return
        elif choice == "a":
            if not url and not slug:
                console.print(
                    "[red]Auto mode requires either a query/URL or a known slug.[/red]"
                )
                continue
            if not url and slug:
                auto_pipeline_existing_slug(slug)
            else:
                auto_pipeline(url)
            continue
        elif choice == "r":
            new_slug = detect_slug_from_disk()
            if new_slug:
                console.print(
                    f"[green]Detected slug from disk: [magenta]{new_slug}[/magenta][/green]"
                )
                slug = new_slug
                url = slug
            else:
                console.print("[yellow]Could not detect slug from disk.[/yellow]")
            continue

        try:
            step = int(choice)
        except ValueError:
            console.print("[red]Invalid choice.[/red]")
            continue

        if step not in SCRIPT_MAP:
            console.print("[red]Invalid step number.[/red]")
            continue

        if step == 1:
            console.print(
                "[bold white]Enter query / YouTube URL for step 1:[/] ",
                end="",
            )
            new_input = sys.stdin.readline().strip()
            if not new_input:
                console.print("[yellow]No input given.[/yellow]")
                continue
            url = new_input
            slug = infer_slug_from_input(url)
        else:
            if not slug:
                console.print(
                    "[red]No slug known yet. Run step 1 first or infer from disk (r).[/red]"
                )
                continue

        code = run_step(step, url if step == 1 else slug)
        if code == ABORT_CODE:
            console.print("[yellow]Step aborted by user.[/yellow]")
        elif code != 0:
            console.print(
                f"[red]Step {step} exited with code {code}.[/red]"
            )
        else:
            console.print(f"[green]Step {step} completed successfully.[/green]")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Karaoke pipeline master orchestrator."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="YouTube URL, search query, or slug. "
             "If omitted, will try to reuse last slug.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Force interactive manual mode (menu).",
    )
    args = parser.parse_args(argv)
    return args


def main(argv=None) -> None:
    args = parse_args(argv or sys.argv[1:])

    url_or_slug = args.input
    last_slug = detect_slug_from_disk()

    if not args.manual:
        reuse_last = False

        if not url_or_slug:
            if last_slug:
                console.print(
                    "[bold white]"
                    f"Audio+lyrics: YouTube search (ENTER = reuse slug '{last_slug}'):[/] ",
                    end="",
                )
                entered = sys.stdin.readline().strip()
                if entered:
                    url_or_slug = entered
                else:
                    url_or_slug = last_slug
                    reuse_last = True
            else:
                console.print(
                    "[bold white]Audio+lyrics: YouTube search or slug:[/] ",
                    end="",
                )
                url_or_slug = sys.stdin.readline().strip()

        if reuse_last and last_slug:
            console.print(
                f"[cyan]Reusing previous slug: [magenta]{last_slug}[/magenta][/cyan]"
            )
            auto_pipeline_existing_slug(last_slug)
        else:
            auto_pipeline(url_or_slug)
        return

    # Manual mode entry: offer to reuse last slug if we have none yet
    if not url_or_slug and last_slug:
        console.print(
            "[bold white]"
            f"Manual mode: ENTER = reuse previous slug '{last_slug}', "
            "or type slug/query/URL:[/] ",
            end="",
        )
        entered = sys.stdin.readline().strip()
        if entered:
            url_or_slug = entered
        else:
            url_or_slug = last_slug

    manual_menu(url_or_slug)


if __name__ == "__main__":
    main()

# end of 0master.py
