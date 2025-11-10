"""
Step 2: Use Demucs to separate vocals and accompaniment.
"""

import os
import sys
import subprocess
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

# === Config ===
MP3_DIR = "mp3s"
STEMS_DIR = "stems"
STEM_MODE = "htdemucs"  # or "demucs", or any valid model
ABORT_CODE = 99

console = Console()

def slugify(filename):
    return os.path.splitext(os.path.basename(filename))[0]

def check_mp3_exists(slug):
    path = os.path.join(MP3_DIR, f"{slug}.mp3")
    if not os.path.exists(path):
        console.print(f"[red]❌ Missing mp3:[/] {path}")
        sys.exit(1)
    return path

def stems_already_exist(slug):
    expected = os.path.join(STEMS_DIR, slug, "vocals.wav")
    return os.path.exists(expected)

def run_demucs(slug, mp3_path):
    if stems_already_exist(slug):
        console.print(f"[yellow]✓ Stems already exist. Skipping separation.[/yellow]")
        return

    console.print(f"[cyan]🎛️  Running Demucs ({STEM_MODE}) on:[/] {mp3_path}")
    cmd = [
        "python3", "-m", "demucs",
        "-n", STEM_MODE,
        "-o", STEMS_DIR,
        mp3_path
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Demucs failed (code {e.returncode})[/red]")
        sys.exit(e.returncode)

    # Move outputs to flat structure (remove nested folder)
    output_base = os.path.join(STEMS_DIR, STEM_MODE, slug)
    target_dir = os.path.join(STEMS_DIR, slug)
    if os.path.isdir(output_base):
        os.rename(output_base, target_dir)
        try:
            os.rmdir(os.path.join(STEMS_DIR, STEM_MODE))
        except Exception:
            pass

    console.print(f"[green]✓ Stems saved to:[/] {target_dir}")

def main():
    if len(sys.argv) < 2:
        console.print("[red]❌ Please provide the slug (filename without extension).[/red]")
        sys.exit(1)
    slug = sys.argv[1]
    mp3_path = check_mp3_exists(slug)

    Path(STEMS_DIR).mkdir(parents=True, exist_ok=True)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Preparing Demucs...", total=None)
        run_demucs(slug, mp3_path)
        progress.update(task, description="Demucs finished!")

    console.print(f"\n[bold green]✅ Stem separation done for:[/] [white]{slug}[/white]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(ABORT_CODE)
