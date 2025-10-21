#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_lyric_fetcher.py — unified lyric scraping backend

Handles all lyric source retrieval:
  • Genius (via Google search)
  • LyricsFreak
  • Lyrics.com

Features:
  • Browser-like User-Agent headers
  • Retry resilience and fallback
  • Debug log output compatible with karaoke_time.py
"""

import subprocess, re, html, time, textwrap, requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Safari/537.36"
)

# -------------------------------------------------------------
# Logging helpers
# -------------------------------------------------------------
def debug_log(log_path, label, content):
    """Append debug info to log if active."""
    if not log_path:
        return
    try:
        snippet = textwrap.shorten(re.sub(r"\s+", " ", content.strip()), width=400, placeholder="...")
        print(f"\n🔎 DEBUG: {label}\n{snippet}\n")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%H:%M:%S')}] {label}\n{content[:4000]}\n")
    except Exception:
        pass

def debug_curl(log_path, cmd):
    """Show and log full curl command for reproduction."""
    curl_str = " ".join(cmd)
    print(f"\n🐚 DEBUG curl: {curl_str}\n")
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%H:%M:%S')}] curl_cmd: {curl_str}\n")
        except Exception:
            pass

# -------------------------------------------------------------
# Utility helpers
# -------------------------------------------------------------
def safe_get(url, retries=3, timeout=10, debug_log_path=None):
    """Perform robust GET with retry/backoff."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code in (403, 429):
                print(f"⚠️  {r.status_code} for {url} — retrying ({attempt}/{retries})…")
                time.sleep(2 * attempt)
                continue
            return r.text
        except Exception as e:
            print(f"⚠️  Request error ({attempt}/{retries}): {e}")
            time.sleep(2 * attempt)
    return ""

def is_valid_lyrics(text: str, source: str, log_path=None) -> bool:
    """Heuristics to reject junk or malformed lyric pages."""
    if not text or len(text.strip()) < 40:
        debug_log(log_path, f"{source}: rejected (too short)", text)
        return False
    if any(x in text.lower() for x in [
        "403", "error", "permission_denied", "api key", "{", "}", "[", "]", "not found", "unauthorized"
    ]):
        debug_log(log_path, f"{source}: rejected (error-like content)", text)
        return False
    if not all(ord(c) < 128 for c in text):
        debug_log(log_path, f"{source}: rejected (non-ASCII characters)", text)
        return False
    symbols = sum(not c.isalnum() and not c.isspace() for c in text)
    ratio = symbols / max(len(text), 1)
    if ratio > 0.25:
        debug_log(log_path, f"{source}: rejected (symbol ratio {ratio:.2f})", text)
        return False
    return True

def sanitize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().replace(" ", "_"))

# -------------------------------------------------------------
# Core fetch logic
# -------------------------------------------------------------
def fetch_lyrics_sources(artist, title, debug=False, debug_log=None):
    """Try multiple lyric sources in order of reliability."""
    artist_q = sanitize_name(artist)
    title_q = sanitize_name(title)

    # ===== Genius =====
    try:
        search_url = f"https://www.google.com/search?q={artist_q}+{title_q}+site:genius.com"
        print(f"🔍 Searching Genius via Google…")
        cmd = ["curl", "-sL", "-A", USER_AGENT, search_url]
        debug_curl(debug_log, cmd)
        html_data = subprocess.check_output(cmd).decode("utf-8", errors="ignore")
        debug_log(debug_log, "Google Search HTML (Genius)", html_data)
        match = re.search(r"https://genius\.com/[a-zA-Z0-9\-]+-lyrics", html_data)
        if match:
            url = match.group(0)
            print(f"✅ Found Genius lyrics URL: {url}")
            page = safe_get(url, debug_log_path=debug_log)
            debug_log(debug_log, "Genius page snippet", page)
            blocks = re.findall(r'<div[^>]+Lyrics__Container[^>]*>(.*?)</div>', page, re.DOTALL)
            lyrics_text = "\n".join(re.sub(r"<.*?>", "", b).strip() for b in blocks)
            clean = html.unescape(lyrics_text).strip()
            if is_valid_lyrics(clean, "Genius", debug_log):
                print("🎯 Genius returned valid lyrics.")
                return clean
            else:
                print("⚠️ Genius returned invalid or empty content.")
    except Exception as e:
        print(f"⚠️ Genius scrape failed: {e}")

    # ===== LyricsFreak =====
    try:
        search_url = f"https://www.lyricsfreak.com/search.php?a=search&type=song&q={artist_q}+{title_q}"
        print(f"🔍 Searching LyricsFreak…")
        cmd = ["curl", "-sL", "-A", USER_AGENT, search_url]
        debug_curl(debug_log, cmd)
        html_data = subprocess.check_output(cmd).decode("utf-8", errors="ignore")
        debug_log(debug_log, "LyricsFreak search HTML", html_data)
        match = re.search(r"/[a-z0-9]/[a-z0-9_\-]+/[a-z0-9_\-]+\.html", html_data)
        if match:
            url = f"https://www.lyricsfreak.com{match.group(0)}"
            print(f"✅ Found LyricsFreak lyrics URL: {url}")
            page = safe_get(url, debug_log_path=debug_log)
            debug_log(debug_log, "LyricsFreak page snippet", page)
            lyrics = re.search(r'<div id="content_h"[^>]*>(.*?)</div>', page, re.DOTALL)
            if lyrics:
                clean = re.sub(r"<.*?>", "", lyrics.group(1))
                clean = html.unescape(clean).strip()
                if is_valid_lyrics(clean, "LyricsFreak", debug_log):
                    print("🎯 LyricsFreak returned valid lyrics.")
                    return clean
    except Exception as e:
        print(f"⚠️ LyricsFreak scrape failed: {e}")

    # ===== Lyrics.com =====
    try:
        search_url = f"https://www.lyrics.com/serp.php?st={title_q}+{artist_q}"
        print(f"🔍 Searching Lyrics.com…")
        debug_log(debug_log, "Lyrics.com fetch URL", search_url)
        html_data = safe_get(search_url, debug_log_path=debug_log)
        debug_log(debug_log, "Lyrics.com search HTML", html_data)
        match = re.search(r"/lyric/[0-9]+/[A-Za-z0-9\-_]+", html_data)
        if match:
            url = f"https://www.lyrics.com{match.group(0)}"
            print(f"✅ Found Lyrics.com lyrics URL: {url}")
            page = safe_get(url, debug_log_path=debug_log)
            debug_log(debug_log, "Lyrics.com page snippet", page)
            lyrics = re.search(r'<pre id="lyric-body-text"[^>]*>(.*?)</pre>', page, re.DOTALL)
            if lyrics:
                clean = re.sub(r"<.*?>", "", lyrics.group(1))
                clean = html.unescape(clean).strip()
                if is_valid_lyrics(clean, "Lyrics.com", debug_log):
                    print("🎯 Lyrics.com returned valid lyrics.")
                    return clean
    except Exception as e:
        print(f"⚠️ Lyrics.com scrape failed: {e}")

        # If all fail
    print("❌ All lyric sources failed.")
    return ""

# -------------------------------------------------------------
# Backward compatibility shim (used by karaoke_generator.py)
# -------------------------------------------------------------
def handle_auto_lyrics(artist, title, debug=False, debug_log=None):
    """
    Backward-compatible alias for fetch_lyrics_sources().
    Required by karaoke_generator.py (LKG-S 2025-10-11).

    Args:
        artist (str): Artist name.
        title (str): Song title.
        debug (bool): Enable verbose output.
        debug_log (str): Optional path for debug log.

    Returns:
        str: Lyrics text if found, otherwise empty string.
    """
    print(f"🎵 handle_auto_lyrics() → fetching lyrics for '{artist} – {title}'")
    return fetch_lyrics_sources(artist, title, debug=debug, debug_log=debug_log)

# end of karaoke_lyric_fetcher.py
