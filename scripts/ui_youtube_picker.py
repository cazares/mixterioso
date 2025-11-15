#!/usr/bin/env python3
# scripts/ui_youtube_picker.py

import os
import sys
import json
import time
import tty
import termios
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# ============================================================================
# ANSI COLORS (your palette)
# ============================================================================
RESET   = "\033[0m"
BOLD    = "\033[1m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
BLUE    = "\033[34m"
WHITE   = "\033[97m"
MAGENTA = "\033[35m"

# ============================================================================
# LOG helper
# ============================================================================
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

# ============================================================================
# Read YouTube API key
# ============================================================================
def get_youtube_api_key() -> str:
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise SystemExit(
            f"{RED}Missing YOUTUBE_API_KEY env var — required for YouTube Data API v3.{RESET}"
        )
    return key

# ============================================================================
# YouTube view-count formatting
# ============================================================================
def format_views(count: Optional[int]) -> str:
    if count is None:
        return "0"

    n = count

    # Billions
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B".rstrip("0").rstrip(".")
    # Millions
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M".rstrip("0").rstrip(".")
    # Thousands
    if n >= 1_000:
        return f"{n/1_000:.1f}k".rstrip("0").rstrip(".")

    return str(n)

# ============================================================================
# YouTube Data API search (returns top 12 videos with view counts)
# ============================================================================
def youtube_search_top12(query: str) -> List[Dict]:
    key = get_youtube_api_key()

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key": key,
        "q": query,
        "part": "snippet",
        "maxResults": 12,
        "type": "video",
        "videoCategoryId": "10",  # music category
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log("YT", f"Search request failed: {e}", RED)
        return []

    data = r.json()
    items = data.get("items", [])
    video_ids = [it["id"]["videoId"] for it in items]

    if not video_ids:
        return []

    # Fetch view counts
    stats_url = "https://www.googleapis.com/youtube/v3/videos"
    stats_params = {
        "key": key,
        "id": ",".join(video_ids),
        "part": "statistics",
        "maxResults": 12,
    }

    try:
        rs = requests.get(stats_url, params=stats_params, timeout=10)
        rs.raise_for_status()
        stats_data = rs.json()
    except Exception as e:
        log("YT", f"Stats request failed: {e}", RED)
        return []

    stats_map = {v["id"]: v["statistics"] for v in stats_data.get("items", [])}

    results = []
    for it in items:
        vid = it["id"]["videoId"]
        snippet = it["snippet"]
        title = snippet.get("title", "(no title)")
        channel = snippet.get("channelTitle", "")
        views = None

        if vid in stats_map:
            try:
                views = int(stats_map[vid].get("viewCount", 0))
            except:
                views = 0

        results.append({
            "video_id": vid,
            "title": title,
            "channel": channel,
            "views": views,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })

    return results

# ============================================================================
# Terminal raw input + arrow key detection
# ============================================================================
def read_key() -> str:
    """
    Capture single keypress including arrow keys.
    Returns:
        "LEFT", "RIGHT", "ENTER", or a literal string like "a", "5", etc.
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        c1 = sys.stdin.read(1)
        if c1 == "\x03":
            raise KeyboardInterrupt

        # ENTER
        if c1 in ("\r", "\n"):
            return "ENTER"

        # Escape sequence?
        if c1 == "\x1b":
            c2 = sys.stdin.read(1)
            if c2 == "[":
                c3 = sys.stdin.read(1)
                if c3 == "C":
                    return "RIGHT"
                if c3 == "D":
                    return "LEFT"
            return ""
        return c1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

# end of chunk 1/3
# ============================================================================
# Pagination helpers
# ============================================================================
def paginate(results: List[Dict], page: int, per_page: int = 3) -> List[Dict]:
    start = (page - 1) * per_page
    end = start + per_page
    return results[start:end]


def total_pages(results: List[Dict], per_page: int = 3) -> int:
    if not results:
        return 1
    return (len(results) + per_page - 1) // per_page


# ============================================================================
# Rendering a page
# ============================================================================
def render_page(
    results: List[Dict],
    page: int,
    highlight_index: Optional[int] = None,
    per_page: int = 3,
) -> None:
    """
    highlight_index is a *global* index (1-based), not page-local.
    """
    pages = total_pages(results, per_page)
    print()
    print(f"{BOLD}{CYAN}Top results (Page {page}/{pages}){RESET}")
    print(f"{WHITE}{'-' * 32}{RESET}")

    page_items = paginate(results, page, per_page)

    for idx, item in enumerate(page_items):
        global_index = (page - 1) * per_page + (idx + 1)
        title = item.get("title", "(no title)")
        views = format_views(item.get("views"))

        if global_index == highlight_index:
            print(
                f"{BOLD}{GREEN}{global_index:2d}. >> {title} << "
                f"{WHITE}({views} views){RESET}"
            )
        else:
            print(
                f"{BOLD}{GREEN}{global_index:2d}.{RESET} "
                f"{title} {YELLOW}({views} views){RESET}"
            )

    print()
    print(
        f"{WHITE}[←] prev  [→] next  "
        f"[1-{len(results)}] choose  "
        f"[q] quit{RESET}"
    )


# ============================================================================
# Resolve global index into (page, index-on-page)
# ============================================================================
def page_for_index(global_index: int, per_page: int = 3) -> int:
    if global_index <= 0:
        return 1
    return (global_index - 1) // per_page + 1


# ============================================================================
# Navigation loop (arrow keys + global input)
# ============================================================================
def interactive_pick(results: List[Dict]) -> Optional[Dict]:
    """
    The main interactive UI:
    - Arrow keys ←/→ change pages
    - Typing a number (1–12) jumps to the correct page + highlights that item
    - ENTER on highlighted item = confirmed
    - ENTER with no highlight = default to first on current page
    - 'q' quits
    """
    if not results:
        log("YT", "No results to pick from.", RED)
        return None

    per_page = 3
    page = 1
    pages = total_pages(results, per_page)
    highlight = None  # global index

    while True:
        render_page(results, page, highlight, per_page)
        key = read_key()

        # Quit
        if key.lower() == "q":
            return None

        # Arrow keys
        if key == "LEFT":
            if page > 1:
                page -= 1
            else:
                pass  # already on first page
            highlight = None
            continue

        if key == "RIGHT":
            if page < pages:
                page += 1
            highlight = None
            continue

        # ENTER
        if key == "ENTER":
            if highlight:
                # Use highlighted choice
                return results[highlight - 1]
            else:
                # Default to first on current page
                global_index = (page - 1) * per_page + 1
                return results[global_index - 1]

        # Number input (could be multi-digit, so collect until non-digit)
        if key.isdigit():
            # Read additional digits (raw mode)
            digits = key
            # allow user to type 1,2 digits for 1–12
            while True:
                nxt = read_key()
                if not nxt.isdigit():
                    # push back the non-digit? We can't.
                    # We'll treat it as termination of number input.
                    break
                digits += nxt

            try:
                choice = int(digits)
            except:
                continue

            if 1 <= choice <= len(results):
                # Jump to page and highlight
                page = page_for_index(choice, per_page)
                highlight = choice
                continue
            else:
                # out of range → ignore
                continue

        # Ignore all other keys
        continue


# end of chunk 2/3
# ============================================================================
# YouTube API Query
# ============================================================================
def youtube_search_api(query: str, max_results: int = 12) -> List[Dict]:
    """
    Uses YouTube Data API v3 to search for videos matching `query`.
    Returns a list of dicts with:
      - videoId
      - title
      - views (int)
    """
    from urllib.parse import urlencode

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        log("YT", "Missing YOUTUBE_API_KEY environment variable.", RED)
        return []

    # First: search → get videoIds
    search_url = (
        "https://www.googleapis.com/youtube/v3/search?"
        + urlencode(
            {
                "key": api_key,
                "q": query,
                "type": "video",
                "part": "id,snippet",
                "maxResults": max_results,
            }
        )
    )

    try:
        r = requests.get(search_url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("YT", f"YouTube API search failed: {e}", RED)
        return []

    items = data.get("items", [])
    if not items:
        log("YT", "YouTube search returned no items.", YELLOW)
        return []

    video_ids = [it["id"]["videoId"] for it in items if "id" in it]

    # Second: fetch stats (views)
    stats_url = (
        "https://www.googleapis.com/youtube/v3/videos?"
        + urlencode(
            {
                "key": api_key,
                "id": ",".join(video_ids),
                "part": "statistics,snippet",
            }
        )
    )

    try:
        r2 = requests.get(stats_url, timeout=10)
        r2.raise_for_status()
        stats_data = r2.json()
    except Exception as e:
        log("YT", f"YouTube stats fetch failed: {e}", RED)
        return []

    out = []
    by_id = {x.get("id"): x for x in stats_data.get("items", [])}

    for it in items:
        vid = it["id"]["videoId"]
        stats_obj = by_id.get(vid, {})
        snippet = stats_obj.get("snippet", {})
        stats = stats_obj.get("statistics", {})
        title = snippet.get("title", it["snippet"].get("title", "(no title)"))
        views = int(stats.get("viewCount", 0))

        out.append(
            {
                "videoId": vid,
                "title": title,
                "views": views,
            }
        )

    return out


# ============================================================================
# Public wrapper
# ============================================================================
def pick_youtube_video(query: str) -> Optional[Dict]:
    """
    High-level function:
    - Queries YouTube API (top 12)
    - Presents 4 pages of 3 items
    - Returns the chosen result dict
      - { "videoId": ..., "title": ..., "views": ... }
    """
    log("YT", f"Querying YouTube for: {query}", CYAN)
    results = youtube_search_api(query, max_results=12)

    if not results:
        log("YT", "No results found.", RED)
        return None

    selected = interactive_pick(results)
    if not selected:
        log("YT", "User cancelled YouTube pick.", YELLOW)
        return None

    log("YT", f'Selected video: "{selected["title"]}"', GREEN)
    return selected


# ============================================================================
# Demo when run directly
# ============================================================================
if __name__ == "__main__":
    print(f"{BOLD}{CYAN}YouTube Picker Demo{RESET}")
    try:
        q = input(f"{WHITE}Enter search query: {RESET}").strip()
    except EOFError:
        q = ""

    if not q:
        print(f"{RED}No query entered. Exiting.{RESET}")
        sys.exit(0)

    res = pick_youtube_video(q)
    print()
    print(f"{GREEN}Chosen:{RESET} {res}")
    print()


# end of ui_youtube_picker.py
