#!/usr/bin/env python3
"""
youtube_audio_picker.py

Download the FIRST, most relevant YouTube result as MP3.
Used by gen_video.sh.

Changes (per Miguel):
- NO --preview-seconds
- NO --preview-interactive
- just: --query and --out
- grab ONLY 1 result (ytsearch1:...)
- force no playlist
- avoid .mp3.mp3 by normalizing the output name
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


def color(msg: str, kind: str = "cyan") -> None:
    if not sys.stdout.isatty():
        print(msg)
        return
    colors = {
        "red": "\033[0;31m",
        "green": "\033[0;32m",
        "yellow": "\033[0;33m",
        "cyan": "\033[0;36m",
        "magenta": "\033[0;35m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    c = colors.get(kind, "")
    r = colors["reset"]
    print(f"{c}{msg}{r}")


def ensure_yt_dlp() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        color("[youtube] yt-dlp not installed. Run:", "yellow")
        color("  pip3 install yt-dlp", "yellow")
        return False


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        return
    color("[youtube] ffmpeg not in PATH — mp3 conversion might fail.", "yellow")


def download_first_as_mp3(query: str, desired_out: str) -> str:
    """
    - If desired_out ends with .mp3, tell yt-dlp to write to base name (no .mp3)
      and let postprocessor produce base.mp3, then rename to desired_out.
    - If desired_out doesn't end with .mp3, just write there.
    """
    import yt_dlp

    Path(desired_out).parent.mkdir(parents=True, exist_ok=True)

    wants_mp3 = desired_out.lower().endswith(".mp3")
    if wants_mp3:
        base_out = desired_out[:-4]  # drop ".mp3"
        final_target = desired_out
    else:
        base_out = desired_out
        final_target = desired_out

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,  # even though ytsearch1 looks like a playlist
        "outtmpl": base_out,
        "quiet": False,      # keep it visible for debugging
    }

    if wants_mp3:
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    url = f"ytsearch1:{query}"
    color(f'[youtube] auto query: "{query}"', "cyan")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    if wants_mp3:
        produced = base_out + ".mp3"
        if produced != final_target:
            try:
                os.replace(produced, final_target)
            except FileNotFoundError:
                # maybe yt-dlp already wrote to final_target
                pass
        return final_target

    return final_target


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download first YouTube audio result (by relevance) as MP3."
    )
    p.add_argument("--query", required=True, help='Search text, e.g. "Artist Title"')
    p.add_argument("--out", required=True, help="Output mp3 path")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not ensure_yt_dlp():
        sys.exit(1)
    ensure_ffmpeg()

    try:
        final_path = download_first_as_mp3(args.query, args.out)
    except Exception as e:
        color(f"[youtube] download failed: {e}", "red")
        sys.exit(1)

    color(f"[OK] Audio saved to {final_path}", "green")


if __name__ == "__main__":
    main()
# end of youtube_audio_picker.py
