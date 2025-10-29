#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube MP3 picker/downloader (macOS-focused)

- Prompts for artist + song OR a custom freeform query.
- Searches YouTube (via yt-dlp), sorts results by view count (most popular first).
- Iterates results until user confirms the correct audio, or refines search.
- Downloads audio as MP3 when FFmpeg is available; otherwise falls back to original audio format.
- After each download, offers:
    1) Play audio in Terminal (afplay, built-in on macOS)
    2) Reveal file in Finder (open -R)
    3) Open containing folder (open <folder>)
    4) Skip

Auto-setup:
- Ensures Python package `yt-dlp` exists; installs it with pip if missing.
- Checks for FFmpeg. If missing, offers to install via Homebrew (brew install ffmpeg), or prints copy/paste instructions.

Run:
    python3 youtube_audio_picker.py
"""

import os
import sys
import shutil
import subprocess
import time
import json
from typing import List, Dict, Optional

# ---------- Utilities ----------

def print_hr():
    print("-" * 70)

def prompt_yn(msg: str, default: Optional[bool] = None) -> bool:
    """
    Ask a yes/no question. Returns True for yes, False for no.
    default=None means no default; user must type y/n.
    """
    while True:
        suffix = " [y/n]: " if default is None else (" [Y/n]: " if default else " [y/N]: ")
        ans = input(f"{msg}{suffix}").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        if ans == "" and default is not None:
            return default
        print("Please type 'y' or 'n'.")

def prompt_choice(msg: str, choices: List[str], default: Optional[str] = None) -> str:
    """
    Generic menu chooser. `choices` are valid lowercase inputs (e.g. ['1','2','3'] or ['y','n']).
    Returns the chosen string.
    """
    while True:
        ans = input(msg).strip().lower()
        if ans in choices:
            return ans
        if ans == "" and default is not None:
            return default
        print(f"Please choose one of: {', '.join(choices)}")

def ensure_pip_package(pkg: str, import_name: Optional[str] = None) -> None:
    """
    Ensure a Python package is installed in the current interpreter environment.
    Uses: python3 -m pip install <pkg>
    """
    name = import_name or pkg
    try:
        __import__(name)
        return
    except Exception:
        print_hr()
        print(f"Python package '{pkg}' not found. Attempting to install with pip3...")
        print("(Using the current interpreter: python3 -m pip)")
        print_hr()
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        except subprocess.CalledProcessError:
            print(f"\nAutomatic install of '{pkg}' failed.")
            print("Please install manually, then re-run this script:\n")
            print(f"    pip3 install {pkg}\n")
            sys.exit(1)
        # Re-import to confirm
        try:
            __import__(name)
        except Exception as e:
            print(f"\nInstalled '{pkg}' but failed to import ({e}).")
            print("Please verify your environment and try:")
            print(f"    pip3 install --upgrade {pkg}\n")
            sys.exit(1)

def have_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def ensure_ffmpeg_available() -> bool:
    """
    Returns True if ffmpeg (and ffprobe) are available.
    If not, on macOS tries to install via Homebrew (with user consent).
    """
    have = have_cmd("ffmpeg") and have_cmd("ffprobe")
    if have:
        return True

    print_hr()
    print("FFmpeg is not available. MP3 conversion needs FFmpeg.")
    print("You can still download the original audio format without FFmpeg.")
    print_hr()

    # Attempt Homebrew install on macOS if brew exists
    if have_cmd("brew"):
        if prompt_yn("Install FFmpeg now via Homebrew? (requires admin rights as needed)", default=True):
            try:
                subprocess.check_call(["brew", "install", "ffmpeg"])
                return have_cmd("ffmpeg") and have_cmd("ffprobe")
            except subprocess.CalledProcessError:
                print("\n'brew install ffmpeg' failed.")
        else:
            print("\nSkipping auto-install.")
    else:
        print("Homebrew not found. Install Homebrew first:")
        print("    /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
        print("Then install FFmpeg:")
        print("    brew install ffmpeg\n")

    print("If you install FFmpeg later, re-run this script for MP3 conversion.")
    return False

def reveal_in_finder(path: str) -> None:
    """
    Reveal a file in Finder (macOS). If reveal fails, open the containing folder.
    """
    try:
        subprocess.run(["open", "-R", path], check=True)
    except subprocess.CalledProcessError:
        folder = os.path.dirname(path) or "."
        subprocess.run(["open", folder], check=False)

def open_folder(path: str) -> None:
    folder = path if os.path.isdir(path) else (os.path.dirname(path) or ".")
    subprocess.run(["open", folder], check=False)

def play_with_afplay(path: str) -> None:
    """
    Play audio in Terminal using afplay (built-in on macOS).
    """
    if not have_cmd("afplay"):
        print("\n'afplay' not found (unexpected on macOS). Opening file with default app instead...")
        subprocess.run(["open", path], check=False)
        return
    try:
        print("\nPlaying audio with 'afplay'... (Ctrl+C to stop)")
        subprocess.run(["afplay", path], check=False)
    except KeyboardInterrupt:
        print("\nStopped playback.")

def pretty_int(n: Optional[int]) -> str:
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    for unit in ["K", "M", "B"]:
        n /= 1000.0
        if n < 1000:
            return f"{n:.1f}{unit}"
    return f"{n:.1f}T"

# ---------- Ensure deps (Python pkgs) ----------

ensure_pip_package("yt-dlp", import_name="yt_dlp")
import yt_dlp  # noqa: E402

# Check FFmpeg (for MP3 conversion); we proceed even if missing (fallback to source audio)
FFMPEG_OK = ensure_ffmpeg_available()

# ---------- YouTube search & download ----------

def yt_search_sorted_by_views(query: str, max_results: int = 25) -> List[Dict]:
    """
    Search YouTube and return entries sorted by view_count desc.
    """
    ydl_opts = {
        "quiet": True,
        "nocheckcertificate": True,
        "skip_download": True,
        # default_search not needed since we pass 'ytsearchN:query'
    }
    entries: List[Dict] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Fetch N results
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        if not info:
            return []
        entries = [e for e in info.get("entries", []) if e]

    # Ensure view_count numeric
    for e in entries:
        if e.get("view_count") is None:
            e["view_count"] = 0

    entries.sort(key=lambda x: x.get("view_count", 0), reverse=True)
    return entries

def build_ydl_opts_for_download(download_dir: str, want_mp3: bool) -> Dict:
    """
    Options for yt-dlp download. If want_mp3 and FFmpeg is present, convert to mp3.
    """
    os.makedirs(download_dir, exist_ok=True)
    base = {
        "quiet": False,
        "restrictfilenames": False,
        "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
        "ignoreerrors": True,
        "noprogress": False,
        "format": "bestaudio/best",
        "postprocessors": [],
    }
    if want_mp3 and FFMPEG_OK:
        base["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    return base

def guess_final_filepath_from_info(info: Dict, download_dir: str, want_mp3: bool) -> Optional[str]:
    """
    Try to determine the final output file path after download/postprocess.
    """
    title = info.get("title")
    if not title:
        return None

    if want_mp3 and FFMPEG_OK:
        candidate = os.path.join(download_dir, f"{title}.mp3")
        if os.path.isfile(candidate):
            return candidate

    # Fallback: look for any file with this title.* in download_dir, prefer audio-like
    exts_priority = [".mp3", ".m4a", ".webm", ".opus", ".mp4", ".wav", ".aac", ".flac"]
    for ext in exts_priority:
        candidate = os.path.join(download_dir, f"{title}{ext}")
        if os.path.isfile(candidate):
            return candidate

    # As a last resort, scan directory for files that start with title (in case sanitizer changed title slightly)
    try:
        for name in os.listdir(download_dir):
            if name.lower().startswith(title.lower()):
                path = os.path.join(download_dir, name)
                if os.path.isfile(path):
                    return path
    except Exception:
        pass

    return None

def download_audio_for_entry(entry: Dict, download_dir: str, want_mp3: bool) -> Optional[str]:
    """
    Download bestaudio and (optionally) convert to mp3. Returns file path or None if failed.
    """
    url = entry.get("webpage_url") or entry.get("url")
    if not url:
        return None

    opts = build_ydl_opts_for_download(download_dir, want_mp3=want_mp3)
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as e:
            print(f"\nDownload failed: {e}")
            return None

    # Post-download: try to compute final path
    path = guess_final_filepath_from_info(info, download_dir, want_mp3=want_mp3)
    if not path:
        print("\nDownloaded, but couldn't locate output file (unexpected).")
    return path

# ---------- Interactive flow ----------

def prompt_search_query() -> Optional[str]:
    print_hr()
    print("Search mode:")
    print("  1) Artist + Song Title (prompted separately)")
    print("  2) Freeform query (e.g., 'john frusciante red hot chili peppers amsterdam guitar solo')")
    mode = prompt_choice("Choose 1 or 2: ", ["1", "2"])

    if mode == "1":
        artist = input("Artist name: ").strip()
        title = input("Song title: ").strip()
        if not artist and not title:
            print("No input provided. Returning to menu.")
            return None
        return f"{artist} {title}".strip()

    # mode == "2"
    query = input("Enter search query: ").strip()
    if not query:
        print("No input provided. Returning to menu.")
        return None
    return query

def post_download_actions(filepath: str) -> None:
    """
    Offer to play, reveal, or open folder after each download.
    """
    while True:
        print_hr()
        print(f"Saved: {os.path.basename(filepath)}")
        print("What next?")
        print("  1) Play in Terminal (afplay)")
        print("  2) Reveal in Finder")
        print("  3) Open containing folder")
        print("  4) Continue (skip)")
        choice = prompt_choice("Choose 1/2/3/4: ", ["1", "2", "3", "4"])
        if choice == "1":
            play_with_afplay(filepath)
            # after playing, keep offering the menu (user may want to reveal too)
        elif choice == "2":
            reveal_in_finder(filepath)
        elif choice == "3":
            open_folder(filepath)
        else:
            break

def main():
    print_hr()
    print("YouTube Audio Picker/Downloader (macOS)")
    print_hr()
    download_dir = os.path.join(os.getcwd(), "downloads")
    print(f"Downloads will be saved to: {download_dir}")

    # Let the user decide if they want MP3 conversion (needs FFmpeg)
    want_mp3 = True
    if not FFMPEG_OK:
        want_mp3 = prompt_yn("FFmpeg missing. Proceed WITHOUT MP3 conversion (download original audio format)?", default=True)

    while True:
        query = prompt_search_query()
        if not query:
            if not prompt_yn("No query entered. Quit?", default=False):
                continue
            else:
                print("\nGoodbye.")
                return

        print_hr()
        print(f"Searching YouTube for: {query!r}")
        results = yt_search_sorted_by_views(query, max_results=25)
        if not results:
            print("No results found.")
            if prompt_yn("Refine your search and try again?", default=True):
                continue
            print("\nGoodbye.")
            return

        print(f"Found {len(results)} results (showing most viewed first).")
        print("You can respond with: y = download, n = next, r = refine, q = quit")

        confirmed = False
        for idx, entry in enumerate(results, start=1):
            title = entry.get("title", "Unknown Title")
            channel = entry.get("channel") or entry.get("uploader", "Unknown")
            views = pretty_int(entry.get("view_count"))
            url = entry.get("webpage_url") or entry.get("url", "")
            print_hr()
            print(f"[{idx}] \"{title}\"")
            print(f"    Channel: {channel}")
            print(f"    Views:   {views}")
            if url:
                print(f"    URL:     {url}")

            act = prompt_choice("Download this audio? (y=download, n=next, r=refine, q=quit): ", ["y", "n", "r", "q"])
            if act == "n":
                continue
            if act == "r":
                break  # refine outer loop
            if act == "q":
                print("\nGoodbye.")
                return

            # act == "y": download this one
            print("\nDownloading best audio" + (" and converting to MP3..." if want_mp3 and FFMPEG_OK else "..."))
            filepath = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=want_mp3)
            if not filepath or not os.path.isfile(filepath):
                print("Failed to download or locate output. Trying next result...")
                continue

            # Offer post-download actions every time
            post_download_actions(filepath)

            # Confirm correctness
            if prompt_yn("Is this the correct audio you were looking for?"):
                print("\n✅ Great! Enjoy your track.")
                confirmed = True
                break
            else:
                print("OK, we'll try the next result...")

        if confirmed:
            return

        # If user chose refine inside the loop
        if act == "r":
            continue

        # Reached end of list
        print_hr()
        if prompt_yn("Reached the end of results. Refine your search and try again?", default=True):
            continue
        else:
            print("\nNo more results. Goodbye.")
            return

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")

# end of youtube_audio_picker.py
