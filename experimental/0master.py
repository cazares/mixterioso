#!/usr/bin/env python3
"""
0master.py – Orchestrate the karaoke pipeline.

Steps:
 1: 1download.py   (takes URL/ID, derives slug, downloads assets)
 2: 2mix.py        (takes slug)
 3: 3time.py       (takes slug, writes timings/<slug>.csv)
 4: 4calibrate.py  (takes slug, writes offsets/<slug>.json)
 5: 5gen_mp4.py    (takes slug, reads timings+offsets, writes mp4s/<slug>.mp4)
"""

import sys
import argparse
import subprocess
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

ABORT_CODE = 99

# script names (must exist next to this file)
SCRIPT_MAP = {
    1: "1download.py",
    2: "2mix.py",
    3: "3time.py",
    4: "4calibrate.py",
    5: "5gen_mp4.py",
}

# simple output dirs (relative to project root, NOT this file)
MP3_DIR = "mp3s"
TXT_DIR = "txts"
STEMS_DIR = "stems"
TIMING_DIR = "timings"   # CSV from 3time.py
OFFSET_DIR = "offsets"   # JSON from 4calibrate.py
MP4_DIR = "mp4s"         # final videos from 5gen_mp4.py
META_DIR = "meta"        # meta/<slug>.json from 1download.py


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

    Used right after step 1 so later steps use the real slug that 1download.py chose.
    """
    meta_dir = Path(META_DIR)
    if meta_dir.exists():
        metas = sorted(meta_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if metas:
            return metas[-1].stem

    mp3_dir = Path(MP3_DIR)
    if mp3_dir.exists():
        mp3s = sorted(mp3_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        if mp3s:
            return mp3s[-1].stem

    return None


def step_done(step: int, slug: str | None) -> bool:
    """Check if a given step appears to be completed based on expected files."""
    if not slug:
        return False

    if step == 1:
        mp3 = Path(MP3_DIR) / f"{slug}.mp3"
        txt = Path(TXT_DIR) / f"{slug}.txt"
        return mp3.exists() and txt.exists()
    elif step == 2:
        vocals = Path(STEMS_DIR) / slug / "vocals.wav"
        marker = Path("mix_done") / f"{slug}.done"
        return vocals.exists() or marker.exists()
    elif step == 3:
        timing = Path(TIMING_DIR) / f"{slug}.csv"
        return timing.exists()
    elif step == 4:
        offset = Path(OFFSET_DIR) / f"{slug}.json"
        return offset.exists()
    elif step == 5:
        mp4 = Path(MP4_DIR) / f"{slug}.mp4"
        return mp4.exists()
    return False


def run_step(step: int, arg: str | None) -> int:
    """Run the script for a given step, passing arg if not None."""
    script_name = SCRIPT_MAP.get(step)
    if not script_name:
        console.print(f"[red]Unknown step {step}[/red]")
        return 1

    script_path = BASE_DIR / script_name
    if not script_path.exists():
        console.print(f"[red]Script not found:[/] {script_path}")
        return 1

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


def manual_menu(url_or_slug: str | None) -> None:
    """Interactive menu mode with status for each step."""
    url = url_or_slug
    slug = infer_slug_from_input(url) if url else None

    while True:
        status_map = {s: ("DONE" if step_done(s, slug) else "PENDING") for s in range(1, 6)}

        table = Table(title="Karaoke Pipeline Menu")
        table.add_column("Step", justify="right")
        table.add_column("Script")
        table.add_column("Status")
        table.add_column("Description")

        table.add_row(
            "1", SCRIPT_MAP[1], status_map[1],
            "Download audio + metadata + lyrics (needs URL/query)",
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
                console.print("[bold white]Enter YouTube URL or ID:[/] ", end="")
                url = sys.stdin.readline().strip()
            if not url:
                console.print("[red]No URL provided. Aborting.[/red]")
                return
            slug = infer_slug_from_input(url)

            for step in range(1, 6):
                arg = url if step == 1 else slug
                if step_done(step, slug):
                    console.print(
                        f"[yellow]Skipping step {step} ({SCRIPT_MAP[step]}), already DONE.[/yellow]"
                    )
                    continue
                code = run_step(step, arg)
                if code == ABORT_CODE:
                    console.print("[yellow]User aborted. Returning to menu.[/yellow]")
                    break
                if code != 0:
                    console.print(
                        f"[red]Step {step} failed (code {code}). Returning to menu.[/red]"
                    )
                    break
                # after step 1, resync slug with what 1download.py actually created
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
            if not url:
                console.print("[bold white]Enter YouTube URL or ID / query:[/] ", end="")
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

        # If we just ran step 1 successfully, auto-detect and store the real slug
        if step == 1 and code == 0:
            detected = detect_slug_from_disk()
            if detected:
                console.print(
                    f"[cyan]Detected slug from assets: [magenta]{detected}[/magenta][/cyan]"
                )
                slug = detected


def auto_pipeline(url: str) -> None:
    """Run steps 1–5 in order, skipping already-done steps, minimal prompts."""
    slug = infer_slug_from_input(url)
    console.print(
        f"[bold]Auto mode:[/] URL=[cyan]{url}[/cyan], initial slug=[magenta]{slug}[/magenta]"
    )

    for step in range(1, 6):
        arg = url if step == 1 else slug
        if step_done(step, slug):
            console.print(
                f"[yellow]Skipping step {step} ({SCRIPT_MAP[step]}), already DONE.[/yellow]"
            )
            continue
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

        # After step 1, refresh slug from disk so steps 2–5 see the real slug
        if step == 1 and code == 0:
            detected = detect_slug_from_disk()
            if detected:
                console.print(
                    f"[cyan]Detected slug from assets: [magenta]{detected}[/magenta][/cyan]"
                )
                slug = detected

    console.print("[bold green]All steps completed. Final MP4 should be ready.[/bold green]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Karaoke pipeline master script (0master.py)."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="YouTube URL/ID (for auto mode) or initial slug/URL (for manual mode).",
    )
    parser.add_argument(
        "--manual",
        "-m",
        action="store_true",
        help="Force interactive manual mode (menu).",
    )
    args = parser.parse_args()

    url_or_slug = args.input

    if not args.manual:
        console.print(
            "[bold white]Automatically run steps 1–5 in order?[/] [green][Y/n][/green] ",
            end="",
        )
        ans = sys.stdin.readline().strip().lower()
        auto = ans in ("", "y", "yes")
        if auto:
            if not url_or_slug:
                console.print(
                    "[bold white]Enter YouTube URL or ID / query:[/] ",
                    end="",
                )
                url_or_slug = sys.stdin.readline().strip()
                if not url_or_slug:
                    console.print("[yellow]No input provided. Exiting.[/yellow]")
                    return
            auto_pipeline(url_or_slug)
            return

    manual_menu(url_or_slug)


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent
    main()

# end of 0master.py
