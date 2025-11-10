"""
Step 1: Download audio (mp3), cover image, and lyrics for a YouTube video.
"""

import os
import sys
import subprocess
import json
import re
import urllib.parse
from pathlib import Path
from rich.console import Console
from rich.progress import Progress
import requests

# Configurable constants
AUDIO_OUT = "mp3s"
TXT_OUT = "txts"
META_OUT = "meta"
COVER_OUT = "covers"
DOWNLOAD_TIMEOUT = 45
GENIUS_TOKEN = os.environ.get("GENIUS_API_TOKEN", None)

console = Console()
progress = Progress(console=console)

def slugify(text):
    return re.sub(r'[\W_]+', '_', text.lower()).strip('_')

def file_exists_msg(path):
    console.print(f"[yellow]✓ Skipping, already exists:[/] {path}")

def download_youtube_audio(url, slug):
    mp3_path = os.path.join(AUDIO_OUT, f"{slug}.mp3")
    if os.path.exists(mp3_path):
        file_exists_msg(mp3_path)
        return mp3_path

    console.print(f"[cyan]⏬ Downloading audio from YouTube for:[/] [bold]{url}[/bold]")
    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--output", f"{AUDIO_OUT}/{slug}.%(ext)s",
        url
    ]
    subprocess.run(cmd, check=True)
    return mp3_path

def extract_metadata(url, slug):
    meta_path = os.path.join(META_OUT, f"{slug}.json")
    if os.path.exists(meta_path):
        file_exists_msg(meta_path)
        return meta_path

    console.print("[cyan]ℹ️  Extracting metadata with yt-dlp...[/cyan]")
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--skip-download",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    Path(META_OUT).mkdir(exist_ok=True)
    with open(meta_path, "w") as f:
        json.dump(data, f, indent=2)
    return meta_path

def download_cover(data, slug):
    cover_path = os.path.join(COVER_OUT, f"{slug}.jpg")
    if os.path.exists(cover_path):
        file_exists_msg(cover_path)
        return cover_path

    thumbnail = data.get("thumbnail") or data.get("thumbnails", [{}])[-1].get("url")
    if not thumbnail:
        console.print("[red]❌ No thumbnail found in metadata.[/red]")
        return None

    Path(COVER_OUT).mkdir(exist_ok=True)
    r = requests.get(thumbnail, timeout=DOWNLOAD_TIMEOUT)
    with open(cover_path, "wb") as f:
        f.write(r.content)
    console.print(f"[green]✓ Downloaded cover to:[/] {cover_path}")
    return cover_path

def search_and_save_lyrics(data, slug):
    txt_path = os.path.join(TXT_OUT, f"{slug}.txt")
    if os.path.exists(txt_path):
        file_exists_msg(txt_path)
        return txt_path

    query = f"{data.get('artist', '')} {data.get('track', '')}".strip()
    title = data.get('title', '')
    console.print(f"[cyan]🎵 Attempting lyrics search for:[/] {query or title}")

    lyrics = None
    if GENIUS_TOKEN:
        try:
            import lyricsgenius
            genius = lyricsgenius.Genius(GENIUS_TOKEN, timeout=10, retries=2)
            song = genius.search_song(title=title)
            if song:
                lyrics = song.lyrics
        except Exception as e:
            console.print(f"[yellow]⚠️ Genius search failed:[/] {e}")

    if not lyrics:
        lyrics = "[Lyrics not found]"

    Path(TXT_OUT).mkdir(exist_ok=True)
    with open(txt_path, "w") as f:
        f.write(lyrics)
    console.print(f"[green]✓ Lyrics saved to:[/] {txt_path}")
    return txt_path

def infer_slug_from_url(url):
    if "v=" in url:
        return slugify(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("v", ["video"])[0])
    elif "youtu.be/" in url:
        return slugify(url.strip().split("/")[-1])
    return slugify(url)

def main():
    if len(sys.argv) < 2:
        console.print("[red]❌ Please provide a YouTube URL or ID as an argument.[/red]")
        sys.exit(1)
    url = sys.argv[1]
    slug = infer_slug_from_url(url)

    # Ensure folders exist
    for folder in (AUDIO_OUT, TXT_OUT, META_OUT, COVER_OUT):
        os.makedirs(folder, exist_ok=True)

    # Core steps
    with progress:
        task = progress.add_task("[blue]Downloading and preparing assets...", total=4)
        mp3 = download_youtube_audio(url, slug); progress.advance(task)
        meta_path = extract_metadata(url, slug); progress.advance(task)
        with open(meta_path) as f:
            metadata = json.load(f)
        cover = download_cover(metadata, slug); progress.advance(task)
        lyrics = search_and_save_lyrics(metadata, slug); progress.advance(task)

    console.print(f"\n[bold green]✅ Download complete! Slug: [white]{slug}[/white][/bold green]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(99)
