#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube MP3 picker/downloader (macOS-focused, now with automated mode).

Interactive usage (manual pick):
    python3 youtube_audio_picker.py
Automated usage (no prompts):
    python3 youtube_audio_picker.py "Artist Name" "Song Title"

NEW (for bash pipeline):
    python3 youtube_audio_picker.py --query "jesus adrian romero sumergeme" --out songs/auto_jesus-adrian-romero-sumergeme.mp3
    python3 youtube_audio_picker.py --artist "Jesús Adrián Romero" --title "Sumérgeme" --out songs/auto_jesus-adrian-romero-sumergeme.mp3
"""
import os, sys, shutil, subprocess, json
from typing import List, Dict, Optional

# ---------- Utilities ----------
def print_hr(): 
    print("-" * 70)

def prompt_yn(msg: str, default: Optional[bool] = None) -> bool:
    """Ask a yes/no question via input (used in interactive mode)."""
    while True:
        suffix = " [y/n]: " if default is None else (" [Y/n]: " if default else " [y/N]: ")
        ans = input(f"{msg}{suffix}").strip().lower()
        if ans in ("y", "yes"): return True
        if ans in ("n", "no"): return False
        if ans == "" and default is not None: return default
        print("Please type 'y' or 'n'.")

def prompt_choice(msg: str, choices: List[str], default: Optional[str] = None) -> str:
    """Generic menu chooser for interactive mode."""
    while True:
        ans = input(msg).strip().lower()
        if ans in choices: return ans
        if ans == "" and default is not None: return default
        print(f"Please choose one of: {', '.join(choices)}")

def ensure_pip_package(pkg: str, import_name: Optional[str] = None) -> None:
    """Ensure a Python package is installed; attempt pip install if not."""
    name = import_name or pkg
    try:
        __import__(name)
        return
    except ImportError:
        print_hr()
        print(f"Python package '{pkg}' not found. Attempting to install via pip...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        except subprocess.CalledProcessError:
            print(f"\nAutomatic install of '{pkg}' failed. Please install it manually and re-run.")
            sys.exit(1)
        try:
            __import__(name)
        except Exception as e:
            print(f"\nInstalled '{pkg}' but failed to import ({e}). Exiting.")
            sys.exit(1)

def have_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def ensure_ffmpeg_available() -> bool:
    """Check for FFmpeg/ffprobe, attempt to install via Homebrew on macOS if missing."""
    have = have_cmd("ffmpeg") and have_cmd("ffprobe")
    if have:
        return True
    print_hr()
    print("FFmpeg is not available. MP3 conversion needs FFmpeg.")
    print_hr()
    if have_cmd("brew"):
        # Prompt to auto-install via Homebrew
        if prompt_yn("Install FFmpeg now via Homebrew? (requires admin password)", default=True):
            try:
                subprocess.check_call(["brew", "install", "ffmpeg"])
                return have_cmd("ffmpeg") and have_cmd("ffprobe")
            except subprocess.CalledProcessError:
                print("\n'brew install ffmpeg' failed.")
        else:
            print("\nSkipping FFmpeg installation.")
    else:
        print("Homebrew not found. Please install FFmpeg manually if needed.")
    return False

def reveal_in_finder(path: str) -> None:
    """Reveal a file in Finder (macOS)."""
    try:
        subprocess.run(["open", "-R", path], check=True)
    except subprocess.CalledProcessError:
        folder = os.path.dirname(path) or "."
        subprocess.run(["open", folder], check=False)

def open_folder(path: str) -> None:
    folder = path if os.path.isdir(path) else (os.path.dirname(path) or ".")
    subprocess.run(["open", folder], check=False)

def play_with_afplay(path: str) -> None:
    """Play audio in Terminal using afplay (macOS)."""
    if not have_cmd("afplay"):
        print("\n'afplay' not found. Opening file with default app...")
        subprocess.run(["open", path], check=False)
        return
    try:
        print("\nPlaying audio (Ctrl+C to stop)...")
        subprocess.run(["afplay", path], check=False)
    except KeyboardInterrupt:
        print("\nStopped playback.")

def pretty_int(n: Optional[int]) -> str:
    """Format a number with K/M/B suffix."""
    if n is None: return "?"
    if n < 1000: return str(n)
    for unit in ["K", "M", "B"]:
        n /= 1000.0
        if n < 1000:
            return f"{n:.1f}{unit}"
    return f"{n:.1f}T"

# ---------- Ensure dependencies ----------
ensure_pip_package("yt-dlp", import_name="yt_dlp")
import yt_dlp  # now we can import

FFMPEG_OK = ensure_ffmpeg_available()

# ---------- YouTube search & download ----------
def yt_search_sorted_by_views(query: str, max_results: int = 25) -> List[Dict]:
    """Search YouTube and return a list of video entries sorted by view count (desc)."""
    ydl_opts = {
        "quiet": True,
        "nocheckcertificate": True,
        "skip_download": True,
    }
    entries: List[Dict] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        if not info:
            return []
        entries = [e for e in info.get("entries", []) if e]
    # Sort results by view_count (highest first)
    for e in entries:
        if e.get("view_count") is None:
            e["view_count"] = 0
    entries.sort(key=lambda x: x.get("view_count", 0), reverse=True)
    return entries

def build_ydl_opts_for_download(download_dir: str, want_mp3: bool) -> Dict:
    """Configure yt-dlp options for downloading best audio, with optional MP3 conversion."""
    os.makedirs(download_dir, exist_ok=True)
    opts = {
        "quiet": False,
        "restrictfilenames": False,
        "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
        "ignoreerrors": True,
        "noprogress": False,
        "format": "bestaudio/best",
        "postprocessors": [],
    }
    if want_mp3 and FFMPEG_OK:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    return opts

def guess_final_filepath_from_info(info: Dict, download_dir: str, want_mp3: bool) -> Optional[str]:
    """Try to determine the final output file path after download/conversion."""
    title = info.get("title")
    if not title:
        return None
    if want_mp3 and FFMPEG_OK:
        candidate = os.path.join(download_dir, f"{title}.mp3")
        if os.path.isfile(candidate):
            return candidate
    # Otherwise, check common extensions
    exts_priority = [".mp3", ".m4a", ".webm", ".opus", ".mp4", ".wav", ".aac", ".flac"]
    for ext in exts_priority:
        candidate = os.path.join(download_dir, f"{title}{ext}")
        if os.path.isfile(candidate):
            return candidate
    # Last resort: any file starting with title (yt-dlp might slightly alter title)
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
    """Download the audio for a given YouTube entry. Returns the file path if successful."""
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
    # Determine the output file path
    path = guess_final_filepath_from_info(info, download_dir, want_mp3=want_mp3)
    if not path:
        print("\nDownloaded, but couldn't locate the audio file.")
    return path

# ---------- NEW helper: deaccent ----------
def deaccent_string(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

# ---------- NEW helper: non-interactive download for bash ----------
def noninteractive_download(query: str, out_path: str) -> bool:
    """
    This is the bit your bash wants:
      python3 youtube_audio_picker.py --query "foo bar" --out /path/to.mp3

    1. search
    2. pick 1st non-cover
    3. download
    4. move/rename to out_path
    """
    print(f'Auto search query: "{query}"')
    results = yt_search_sorted_by_views(query, max_results=15)
    if not results:
        # try deaccented
        plain = deaccent_string(query)
        if plain != query:
            print("[WARN] No results, retrying without accents…")
            results = yt_search_sorted_by_views(plain, max_results=15)
    if not results:
        print("[ERROR] No results found on YouTube for the query.")
        return False

    download_dir = os.path.dirname(out_path) or "."
    os.makedirs(download_dir, exist_ok=True)

    # prefer non-cover/instrumental
    def is_bad_title(t: str) -> bool:
        t = t.lower()
        bad = ["cover", "karaoke", "instrumental", "live", "en vivo"]
        return any(b in t for b in bad)

    candidates = [e for e in results if not is_bad_title(e.get("title") or "")]
    if not candidates:
        candidates = results

    for entry in candidates:
        title = entry.get("title", "Unknown")
        print(f'Auto-selected: "{title}"')
        fp = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=True)
        if fp and os.path.isfile(fp):
            # rename/move to out_path
            if os.path.abspath(fp) != os.path.abspath(out_path):
                try:
                    shutil.move(fp, out_path)
                except Exception as e:
                    print(f"[WARN] could not rename to {out_path}: {e}")
            print(f"[OK] Audio saved to {out_path}")
            return True
        else:
            print("[WARN] download failed for this candidate, trying next…")

    print("[ERROR] Audio download failed for all candidate results.")
    return False

# ---------- Main interactive flow ----------
def prompt_search_query() -> Optional[str]:
    print_hr()
    print("Search mode:")
    print("  1) Artist + Song Title (separate prompts)")
    print("  2) Freeform query")
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
    """Offer to play or reveal the file (interactive mode)."""
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
        elif choice == "2":
            reveal_in_finder(filepath)
        elif choice == "3":
            open_folder(filepath)
        else:
            break

def main():
    print_hr()
    print("YouTube Audio Picker/Downloader (Interactive Mode)")
    print_hr()
    download_dir = os.path.join(os.getcwd(), "downloads")
    print(f"Downloads will be saved to: {download_dir}")
    # Ask if user wants MP3 conversion (if FFmpeg missing, default to original audio)
    want_mp3 = True
    if not FFMPEG_OK:
        want_mp3 = prompt_yn("FFmpeg missing. Proceed WITHOUT MP3 conversion?", default=True)
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
        print(f"Found {len(results)} results (sorted by views).")
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
                break  # jump out to outer loop to refine query
            if act == "q":
                print("\nGoodbye.")
                return
            # User chose to download this entry
            print("\nDownloading best audio" + (" and converting to MP3..." if want_mp3 and FFMPEG_OK else "..."))
            filepath = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=want_mp3)
            if not filepath or not os.path.isfile(filepath):
                print("Failed to download this result. Trying next...")
                continue
            # Offer post-download actions
            post_download_actions(filepath)
            # Confirm if this was the correct audio
            if prompt_yn("Is this the correct audio?"):
                print("\n✅ Download confirmed. Enjoy your track!")
                confirmed = True
                break
            else:
                print("OK, trying the next result...")
        if confirmed:
            return
        if act == "r":
            continue  # user chose to refine search
        # End of results
        print_hr()
        if prompt_yn("Reached end of results. Refine search and try again?", default=True):
            continue
        else:
            print("\nNo more results. Goodbye.")
            return

# --- Automated / script entrypoint ----------------------------------------
if __name__ == "__main__":
    # NEW: real CLI path for bash
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--query", help="Full YouTube search query (preferred for automation).")
    ap.add_argument("--artist", help="Artist (will be combined with --title).")
    ap.add_argument("--title", help="Title (will be combined with --artist).")
    ap.add_argument("--out", help="Output path (mp3).")
    # we don't want to break the old positional auto-mode, so we parse only known
    args, unknown = ap.parse_known_args()

    # 1) if called with flags, do flag mode
    if args.query or args.artist or args.title:
        # build query
        if args.query:
            q = args.query.strip()
        else:
            art = args.artist or ""
            tit = args.title or ""
            q = f"{art} {tit}".strip()
        out_path = args.out or os.path.join(os.getcwd(), "songs", "auto_download.mp3")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        ok = noninteractive_download(q, out_path)
        sys.exit(0 if ok else 1)

    # 2) if they passed positionals (old style): keep your old behavior
    if len(sys.argv) > 1:
        # Auto mode: use provided args as search query
        query = " ".join(sys.argv[1:]).strip()
        print(f"Auto search query: \"{query}\"")
        results = yt_search_sorted_by_views(query, max_results=15)
        if not results:
            print("No results found on YouTube for the query.")
            sys.exit(1)
        # Filter out obvious covers, karaoke or live versions to prefer official audio
        filtered = []
        for e in results:
            title = (e.get("title") or "").lower()
            if any(term in title for term in ["cover", "karaoke", "instrumental"]):
                continue
            filtered.append(e)
        if not filtered:
            filtered = results
        entry = filtered[0]
        title = entry.get("title", "Unknown Title")
        channel = entry.get("channel") or entry.get("uploader", "Unknown")
        views = pretty_int(entry.get("view_count"))
        print(f"Auto-selected: \"{title}\" ({views} views) by {channel}")
        download_dir = os.path.join(os.getcwd(), "songs")
        os.makedirs(download_dir, exist_ok=True)
        want_mp3 = True
        if not FFMPEG_OK:
            print("(FFmpeg not available, downloading without MP3 conversion)")
            want_mp3 = False
        print("Downloading audio...")
        filepath = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=want_mp3)
        if not filepath or not os.path.isfile(filepath):
            # try alternative results if first fails
            for alt in filtered[1:]:
                title = alt.get("title", "")
                channel = alt.get("channel") or alt.get("uploader", "")
                views = pretty_int(alt.get("view_count"))
                print(f"Trying next result: \"{title}\" ({views} views) by {channel}...")
                filepath = download_audio_for_entry(alt, download_dir=download_dir, want_mp3=want_mp3)
                if filepath and os.path.isfile(filepath):
                    break
        if not filepath or not os.path.isfile(filepath):
            print("Audio download failed for all candidate results.")
            sys.exit(1)
        # Rename the downloaded file to our standardized filename
        try:
            from lyrics_fetcher import slug_hyphen  # use same slug logic for consistency
        except Exception:
            # fallback: simple
            def slug_hyphen(s: str) -> str:
                return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")
        artist_arg = sys.argv[1]; title_arg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        slug_base = f"{slug_hyphen(artist_arg)}-{slug_hyphen(title_arg)}".strip("-")
        orig_ext = os.path.splitext(filepath)[1] or ""
        target_path = os.path.join(download_dir, f"auto_{slug_base}{orig_ext}")
        try:
            if os.path.abspath(filepath) != os.path.abspath(target_path):
                shutil.move(filepath, target_path)
            filepath = target_path
        except Exception as e:
            print(f"Warning: could not rename file: {e}")
        print(f"Saved audio to {filepath}")
        sys.exit(0)
    else:
        # 3) no args → interactive
        try:
            main()
        except KeyboardInterrupt:
            print("\nInterrupted. Goodbye.")
# end of youtube_audio_picker.py
