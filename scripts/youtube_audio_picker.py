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

NEW (preview candidates, macOS):
    python3 youtube_audio_picker.py --query "jesus adrian romero sumergeme" --out songs/...mp3 --preview-seconds 10 --preview-interactive

NEW (URL direct):
    python3 youtube_audio_picker.py --query "https://www.youtube.com/watch?v=4GE6VnZrFjg" --out songs/auto_x.mp3
"""
import os, sys, shutil, subprocess, json, tempfile, unicodedata
from typing import List, Dict, Optional

# ---------- Utilities ----------
def print_hr():
    print("-" * 70)

def prompt_yn(msg: str, default: Optional[bool] = None) -> bool:
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
    while True:
        ans = input(msg).strip().lower()
        if ans in choices:
            return ans
        if ans == "" and default is not None:
            return default
        print(f"Please choose one of: {', '.join(choices)}")

def ensure_pip_package(pkg: str, import_name: Optional[str] = None) -> None:
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
    have = have_cmd("ffmpeg") and have_cmd("ffprobe")
    if have:
        return True
    print_hr()
    print("FFmpeg is not available. MP3 conversion needs FFmpeg.")
    print_hr()
    if have_cmd("brew"):
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
    try:
        subprocess.run(["open", "-R", path], check=True)
    except subprocess.CalledProcessError:
        folder = os.path.dirname(path) or "."
        subprocess.run(["open", folder], check=False)

def open_folder(path: str) -> None:
    folder = path if os.path.isdir(path) else (os.path.dirname(path) or ".")
    subprocess.run(["open", folder], check=False)

def play_with_afplay(path: str) -> None:
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
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    for unit in ["K", "M", "B"]:
        n /= 1000.0
        if n < 1000:
            return f"{n:.1f}{unit}"
    return f"{n:.1f}T"

# ---------- Ensure dependencies ----------
ensure_pip_package("yt-dlp", import_name="yt_dlp")
import yt_dlp

FFMPEG_OK = ensure_ffmpeg_available()

# ---------- matching helpers ----------
def normalize_for_match(s: str) -> str:
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s

def deaccent_string(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def is_youtube_url(q: str) -> bool:
    q = q.strip()
    return q.startswith("https://www.youtube.com/") or q.startswith("https://youtu.be/") or q.startswith("http://www.youtube.com/") or q.startswith("http://youtu.be/")

# ---------- YouTube search & download ----------
def yt_search_relevance(query: str, max_results: int = 25) -> List[Dict]:
    """
    IMPORTANT: we do NOT re-sort by view_count here.
    We keep YouTube's native relevance order (like browser).
    """
    ydl_opts = {
        "quiet": True,
        "nocheckcertificate": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    if not info:
        return []
    entries = [e for e in info.get("entries", []) if e]
    return entries  # keep original order

def build_ydl_opts_for_download(download_dir: str, want_mp3: bool) -> Dict:
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
    title = info.get("title")
    if not title:
        return None
    if want_mp3 and FFMPEG_OK:
        candidate = os.path.join(download_dir, f"{title}.mp3")
        if os.path.isfile(candidate):
            return candidate
    exts_priority = [".mp3", ".m4a", ".webm", ".opus", ".mp4", ".wav", ".aac", ".flac"]
    for ext in exts_priority:
        candidate = os.path.join(download_dir, f"{title}{ext}")
        if os.path.isfile(candidate):
            return candidate
    try:
        for name in os.listdir(download_dir):
            if name.lower().startswith((title or "").lower()):
                path = os.path.join(download_dir, name)
                if os.path.isfile(path):
                    return path
    except Exception:
        pass
    return None

def download_audio_for_entry(entry: Dict, download_dir: str, want_mp3: bool) -> Optional[str]:
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
    path = guess_final_filepath_from_info(info, download_dir, want_mp3=want_mp3)
    if not path:
        print("\nDownloaded, but couldn't locate the audio file.")
    return path

# ---------- preview support ----------
def download_preview_for_entry(entry: Dict, seconds: int = 10) -> Optional[str]:
    url = entry.get("webpage_url") or entry.get("url")
    if not url:
        return None

    tmp_dir = tempfile.mkdtemp(prefix="yt_prev_")
    raw_path = os.path.join(tmp_dir, "raw.m4a")
    preview_path = os.path.join(tmp_dir, "preview.wav")

    opts = {
        "quiet": True,
        "outtmpl": raw_path,
        "format": "bestaudio/best",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([url])
        except Exception as e:
            print(f"[preview] download failed: {e}")
            return None

    if not os.path.exists(raw_path):
        candidates = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)]
        if candidates:
            raw_path = candidates[0]
        else:
            return None

    if not FFMPEG_OK:
        return raw_path

    try:
        subprocess.check_call([
            "ffmpeg", "-y", "-i", raw_path, "-t", str(seconds),
            "-ac", "2", "-ar", "48000", preview_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return preview_path
    except Exception as e:
        print(f"[preview] ffmpeg trim failed: {e}")
        return raw_path

def preview_and_confirm_entry(entry: Dict, seconds: int = 10) -> bool:
    print(f"[preview] grabbing ~{seconds}s to preview…")
    prev = download_preview_for_entry(entry, seconds=seconds)
    if not prev or not os.path.exists(prev):
        print("[preview] could not build preview, skipping this candidate.")
        return False
    play_with_afplay(prev)
    return prompt_yn("Is THIS the right audio?", default=True)

# ---------- noninteractive (relevance-first + required-title) ----------
def noninteractive_download(
    query: str,
    out_path: str,
    preview_seconds: int = 0,
    preview_interactive: bool = False,
    required_title: Optional[str] = None,
) -> bool:
    # 0) direct URL? just download it, no drama
    if is_youtube_url(query):
        print(f"[direct-url] downloading from {query}")
        entry = {"webpage_url": query, "title": "direct-url"}
        download_dir = os.path.dirname(out_path) or "."
        os.makedirs(download_dir, exist_ok=True)
        fp = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=True)
        if fp and os.path.isfile(fp):
            if os.path.abspath(fp) != os.path.abspath(out_path):
                try:
                    shutil.move(fp, out_path)
                except Exception as e:
                    print(f"[WARN] cannot move to {out_path}: {e}")
            print(f"[OK] Audio saved to {out_path}")
            return True
        print("[ERROR] direct-url download failed")
        return False

    print(f'Auto search query: "{query}"')
    results = yt_search_relevance(query, max_results=20)
    if not results:
        plain = deaccent_string(query)
        if plain != query:
            print("[WARN] No results, retrying without accents…")
            results = yt_search_relevance(plain, max_results=20)
    if not results:
        print("[ERROR] No results found on YouTube for the query.")
        return False

    download_dir = os.path.dirname(out_path) or "."
    os.makedirs(download_dir, exist_ok=True)

    def is_bad_title(t: str) -> bool:
        t = t.lower()
        bad = ["cover", "karaoke", "instrumental", "live", "en vivo"]
        return any(b in t for b in bad)

    candidates = [e for e in results if not is_bad_title((e.get("title") or ""))]
    if not candidates:
        candidates = results

    # 👉 required-title boost (your "Me Dice Que Me Ama" case)
    if required_title:
        norm_req = normalize_for_match(required_title)
        strict = []
        loose = []
        others = []
        for e in candidates:
            yt_title = e.get("title") or ""
            norm_yt = normalize_for_match(yt_title)
            if norm_yt == norm_req:
                strict.append(e)   # exact match
            elif norm_req in norm_yt:
                loose.append(e)    # contains
            else:
                others.append(e)
        if strict or loose:
            candidates = strict + loose + others
        else:
            print(f"[WARN] No YT results contained required title: {required_title!r} (accent-insensitive)")
            # keep original candidates in relevance order

    # now walk the list in THIS order (YT relevance + our title boost)
    for entry in candidates:
        title = entry.get("title", "Unknown")
        ch = entry.get("channel") or entry.get("uploader", "Unknown")
        print(f'[auto] candidate: "{title}" by {ch}')

        if preview_interactive and preview_seconds > 0:
            ok = preview_and_confirm_entry(entry, seconds=preview_seconds)
            if not ok:
                print("[auto] user said NO → trying next candidate…")
                continue
            print("[auto] downloading full audio for chosen candidate…")
            fp = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=True)
            if fp and os.path.isfile(fp):
                if os.path.abspath(fp) != os.path.abspath(out_path):
                    try:
                        shutil.move(fp, out_path)
                    except Exception as e:
                        print(f"[WARN] could not move to {out_path}: {e}")
                print(f"[OK] Audio saved to {out_path}")
                return True
            else:
                print("[WARN] full download failed, trying next candidate…")
                continue
        else:
            fp = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=True)
            if fp and os.path.isfile(fp):
                if os.path.abspath(fp) != os.path.abspath(out_path):
                    try:
                        shutil.move(fp, out_path)
                    except Exception as e:
                        print(f"[WARN] could not move to {out_path}: {e}")
                print(f"[OK] Audio saved to {out_path}")
                return True
            else:
                print("[WARN] download failed for this candidate, trying next…")

    print("[ERROR] Audio download failed for all candidate results.")
    return False

# ---------- Interactive flow (unchanged) ----------
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
    query = input("Enter search query: ").strip()
    if not query:
        print("No input provided. Returning to menu.")
        return None
    return query

def post_download_actions(filepath: str) -> None:
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
        results = yt_search_relevance(query, max_results=25)
        if not results:
            print("No results found.")
            if prompt_yn("Refine your search and try again?", default=True):
                continue
            print("\nGoodbye.")
            return
        print(f"Found {len(results)} results (YT relevance order).")
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
                break
            if act == "q":
                print("\nGoodbye.")
                return
            print("\nDownloading best audio" + (" and converting to MP3..." if want_mp3 and FFMPEG_OK else "..."))
            filepath = download_audio_for_entry(entry, download_dir=download_dir, want_mp3=want_mp3)
            if not filepath or not os.path.isfile(filepath):
                print("Failed to download this result. Trying next...")
                continue
            post_download_actions(filepath)
            if prompt_yn("Is this the correct audio?"):
                print("\n✅ Download confirmed. Enjoy your track!")
                confirmed = True
                break
            else:
                print("OK, trying the next result...")
        if confirmed:
            return
        if act == "r":
            continue
        print_hr()
        if prompt_yn("Reached end of results. Refine search and try again?", default=True):
            continue
        else:
            print("\nNo more results. Goodbye.")
            return

# --- Automated / script entrypoint ----------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--query", help="Full YouTube search query (preferred for automation).")
    ap.add_argument("--artist", help="Artist (will be combined with --title).")
    ap.add_argument("--title", help="Title (will be combined with --artist).")
    ap.add_argument("--out", help="Output path (mp3).")
    ap.add_argument("--preview-seconds", type=int, default=0, help="Download + play only N seconds to confirm.")
    ap.add_argument("--preview-interactive", action="store_true", help="Play the preview and ask to confirm; if no, try next result.")
    args, unknown = ap.parse_known_args()

    # 1) flag mode (what your bash calls)
    if args.query or args.artist or args.title:
        if args.query:
            q = args.query.strip()
            required_title = None  # freeform query: we don't force title
        else:
            art = args.artist or ""
            tit = args.title or ""
            q = f"{art} {tit}".strip()
            required_title = tit or None
        out_path = args.out or os.path.join(os.getcwd(), "songs", "auto_download.mp3")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        ok = noninteractive_download(
            q,
            out_path,
            preview_seconds=args.preview_seconds,
            preview_interactive=args.preview_interactive,
            required_title=required_title,
        )
        sys.exit(0 if ok else 1)

    # 2) positional auto-mode (kept)
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:]).strip()
        ok = noninteractive_download(query, os.path.join(os.getcwd(), "songs", "auto_download.mp3"))
        sys.exit(0 if ok else 1)

    # 3) fallback to interactive
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")
# end of youtube_audio_picker.py
