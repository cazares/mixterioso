#!/usr/bin/env python3
"""
2mix.py – choose how to treat audio for this song.

Options:
 1) Use original full mix (no Demucs)
 2) Run Demucs to create stems (for karaoke/instrumental use)
"""

import os
import sys
import subprocess
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt

console = Console()

MP3_DIR = "mp3s"
STEMS_DIR = "stems"
MIX_MARKER_DIR = "mix_done"
DEMUCS_MODEL = "htdemucs"  # change if you use a different model
ABORT_CODE = 99


def mp3_path_for_slug(slug: str) -> Path:
    return Path(MP3_DIR) / f"{slug}.mp3"


def stems_exist(slug: str) -> bool:
    return (Path(STEMS_DIR) / slug / "vocals.wav").exists()


def marker_path(slug: str) -> Path:
    return Path(MIX_MARKER_DIR) / f"{slug}.done"


def ensure_dirs():
    Path(MP3_DIR).mkdir(exist_ok=True)
    Path(STEMS_DIR).mkdir(parents=True, exist_ok=True)
    Path(MIX_MARKER_DIR).mkdir(exist_ok=True)


def run_demucs(slug: str, mp3_path: Path):
    if stems_exist(slug):
        console.print(f"[yellow]Stems already exist for '{slug}', not re-running Demucs.[/yellow]")
        return

    console.print(f"[cyan]🎛 Running Demucs ({DEMUCS_MODEL}) on:[/] {mp3_path}")
    cmd = [
        sys.executable, "-m", "demucs",
        "-n", DEMUCS_MODEL,
        "-o", STEMS_DIR,
        str(mp3_path),
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Demucs failed with code {e.returncode}[/red]")
        sys.exit(e.returncode)

    # Demucs output layout: stems/DEMUCS_MODEL/<basename>/
    src_dir = Path(STEMS_DIR) / DEMUCS_MODEL / mp3_path.stem
    dst_dir = Path(STEMS_DIR) / mp3_path.stem
    if src_dir.exists() and not dst_dir.exists():
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        src_dir.rename(dst_dir)
        # try to clean up empty model dir
        try:
            (Path(STEMS_DIR) / DEMUCS_MODEL).rmdir()
        except OSError:
            pass

    console.print(f"[green]✓ Stems ready at:[/] {dst_dir}")


def main():
    if len(sys.argv) < 2:
        console.print("[red]❌ Usage:[/] python 2mix.py [slug]")
        sys.exit(1)

    slug = sys.argv[1].strip()
    ensure_dirs()

    mp3_path = mp3_path_for_slug(slug)
    if not mp3_path.exists():
        console.print(f"[red]❌ MP3 not found for slug '{slug}':[/] {mp3_path}")
        sys.exit(1)

    # If we've already configured mix for this slug, do nothing
    if marker_path(slug).exists():
        console.print(f"[yellow]Mix already configured for '{slug}', nothing to do.[/yellow]")
        sys.exit(0)

    console.print(f"[bold]Audio mix options for slug:[/] [magenta]{slug}[/magenta]\n")
    console.print(" [green]1[/green]) Use original full mix (no Demucs, keep MP3 as-is)  [default]")
    console.print(" [green]2[/green]) Run Demucs to create stems (for vocal reduction)")
    console.print(" [green]q[/green]) Cancel this step\n")

    choice = Prompt.ask(
        "[bold white]Choose option[/] [1/2/q]",
        choices=["1", "2", "q"],
        default="1",
        show_choices=False,
    )

    if choice == "q":
        console.print("[yellow]Step 2 cancelled by user.[/yellow]")
        sys.exit(ABORT_CODE)

    if choice == "1":
        # No Demucs, just mark as done
        marker_path(slug).touch()
        console.print("[green]✓ Using original audio. Demucs will NOT be run for this song.[/green]")
        sys.exit(0)

    if choice == "2":
        run_demucs(slug, mp3_path)
        marker_path(slug).touch()
        console.print("[green]✓ Mix step configured with Demucs stems.[/green]")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(ABORT_CODE)

# end of 2mix.py
