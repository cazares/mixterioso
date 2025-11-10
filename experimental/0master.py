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
TIMING_DIR = PROJECT_ROOT / "timings"   # CSV from 3time.py
OFFSET_DIR = PROJECT_ROOT / "offsets"   # JSON from 4calibrate.py
MP4_DIR = PROJECT_ROOT / "mp4s"         # final videos from 5gen_mp4.py
META_DIR = PROJECT_ROOT / "meta"        # meta/<slug>.json from 1download.py


def infer_slug_from_input(input_str: str) -> str:
    """Infer a slug from a YouTube URL or generic string."""
    import urllib.parse

    s = input_str.strip()
    # YouTube watch URL
    if "youtube.com" in s and "v=" in s:
        parsed = urllib.parse.urlparse(s)
        qs = urllib.parse.parse_qs(parsed.query)
        vid = qs.get("v", [""])[0]
        if vid:
            s = vid
    # youtu.be short URL
    elif "youtu.be/" in s:
        s = s.rstrip("/").split("/")[-1]

    slug = "".join(c for c in s.lower() if c.isalnum() or c in "-_").strip("_-")
    return slug[:64] or "song"


def detect_slug_from_disk() -> str | None:
    """
    Detect the most recent slug based on meta/*.json or mp3s/*.mp3.

    Used after step 1 so later steps use the real slug that 1download.py chose,
    and as a "last used slug" when starting without arguments.
    """
    if META_DIR.exists():
        metas = sorted(META_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if metas:
            return metas[-1].stem

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

    # Special handling: for step 5, always force regeneration by deleting existing MP4.
    if step == 5 and arg:
        slug = arg.strip()
        if slug:
            mp4_path = MP4_DIR / f"{slug}.mp4"
            if mp4_path.exists():
                try:
                    console.print(
                        f"[yellow][MP4] Removing existing file to regenerate:[/] {mp4_path}"
                    )
                    mp4_path.unlink()
                except OSError as e:
                    console.print(
                        f"[red][MP4] Failed to delete existing MP4 ({mp4_path}): {e}[/red]"
                    )
                    # Still continue; 5gen_mp4.py may choose to overwrite.

    if step == 3:
        slug = (arg or "").strip()
        if not slug:
            console.print("[red]Step 3 requires a slug but none was provided.[/red]")
            return 1

        txt_path = TXT_DIR / f"{slug}.txt"
        audio_path = MP3_DIR / f"{slug}.mp3"
        timings_path = TIMING_DIR / f"{slug}.csv"

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
            console.print(
                f"[red]Step {step} failed with code {code}. Stopping.[/red]"
            )
            sys.exit(code)
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
            "Offset calibration (curses → offsets JSON)",
        )
        table.add_row(
            "5", SCRIPT_MAP[5], status_map[5],
            "Final MP4 generation (ffmpeg, uses offset)",
        )

        console.print()
        console.print(table)
        console.print(
            "[bold white]Choose step [1–5], 'a' for all 1–5, or 'q' to quit:[/] ",
            end="",
        )
        choice = sys.stdin.readline().strip().lower()

        if choice == "q":
            console.print("[green]Exiting manual mode.[/green]")
            return

        if choice == "a":
            if not url:
                console.print(
                    "[bold white]Audio+lyrics: YouTube url / search:[/] ",
                    end="",
                )
                url = sys.stdin.readline().strip()
            if not url:
                console.print("[red]No input provided. Aborting.[/red]")
                return
            slug = infer_slug_from_input(url)

            # Run all 5 steps, no skip-done optimization
            for step in range(1, 6):
                arg = url if step == 1 else slug
                code = run_step(step, arg)
                if code == ABORT_CODE:
                    console.print("[yellow]User aborted. Returning to menu.[/yellow]")
                    break
                if code != 0:
                    console.print(
                        f"[red]Step {step} failed (code {code}). Returning to menu.[/red]"
                    )
                    break
                if step == 1 and code == 0:
                    detected = detect_slug_from_disk()
                    if detected:
                        console.print(
                            f"[cyan]Detected slug from assets: [magenta]{detected}[/magenta][/cyan]"
                        )
                        slug = detected
            continue

        if choice not in {"1", "2", "3", "4", "5"}:
            console.print("[yellow]Invalid choice.[/yellow]")
            continue

        step = int(choice)

        if step == 1:
            console.print(
                "[bold white]Audio+lyrics: YouTube url / search:[/] ",
                end="",
            )
            url = sys.stdin.readline().strip()
            arg = url
        else:
            if not slug:
                console.print(
                    "[bold white]Enter slug (e.g. under_the_bridge):[/] ",
                    end="",
                )
                slug = sys.stdin.readline().strip()
            arg = slug

        if step_done(step, slug):
            console.print(
                f"[yellow]Step {step} already DONE. Re-run anyway? [y/N]:[/] ",
                end="",
            )
            ans = sys.stdin.readline().strip().lower()
            if ans not in ("y", "yes"):
                continue

        code = run_step(step, arg)
        if code == ABORT_CODE:
            console.print("[yellow]User aborted step.[/yellow]")
        elif code != 0:
            console.print(f"[red]Step {step} failed (code {code}).[/red]")
        else:
            console.print(f"[green]Step {step} completed.[/green]")

        if step == 1 and code == 0:
            detected = detect_slug_from_disk()
            if detected:
                console.print(
                    f"[cyan]Detected slug from assets: [magenta]{detected}[/magenta][/cyan]"
                )
                slug = detected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Karaoke pipeline master script (0master.py)."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Initial query / YT URL / slug (optional).",
    )
    parser.add_argument(
        "--manual",
        "-m",
        action="store_true",
        help="Force interactive manual mode (menu).",
    )
    args = parser.parse_args()

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
                    reuse_last = True
            else:
                console.print(
                    "[bold white]Audio+lyrics: YouTube url / search:[/] ",
                    end="...",
                )
                sys.stdout.flush()
                console.print("\r[bold white]Audio+lyrics: YouTube url / search:[/] ", end="")
                url_or_slug = sys.stdin.readline().strip()
                if not url_or_slug:
                    console.print("[yellow]No input provided. Exiting.[/yellow]")
                    return

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
