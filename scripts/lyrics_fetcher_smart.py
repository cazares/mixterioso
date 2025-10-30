#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_fetcher_smart.py

Try multiple lyric sources, clean/junk-strip them, normalize Spanish textisms
(e.g. "q" -> "que"), then pick the "best" version (most lines / chars).

Usage:
    python3 lyrics_fetcher_smart.py "Artist" "Title" -o auto_lyrics/artist-title.txt

Notes:
- This is meant to be a drop-in smarter version of lyrics_fetcher.py.
- We *attempt* to import your existing scripts/lyrics_fetcher.py and use it as
  one of the sources, so your current working fetch still works.
- You can add more sources in SOURCE_FUNCS below.
"""

import os
import sys
import re
import argparse
from typing import List, Tuple, Callable, Optional

# ------------------------------------------------------------
# 1. helpers
# ------------------------------------------------------------
def slug_hyphen(s: str) -> str:
    s = s.strip().lower()
    # deaccent common Spanish chars
    trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunaeiouun")
    s = s.translate(trans)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s

JUNK_PREFIXES = [
    "read more",
    "90 contributors", "80 contributors", "70 contributors",
    "translations",
    "português", "portugus", "trke", "espaol", "español", "inglés",
    "embed", "you might also like",
    "lyrics",  # often a bare "Song Title Lyrics"
]

JUNK_CONTAINS = [
    "the first single off of",
    "is a song by",
    "genius annotation",
    "copyright",
    "©",
    "all rights reserved",
    "provided by musixmatch",
]

def is_junk_line(line: str) -> bool:
    l = line.strip().lower()
    if not l:
        return True
    for p in JUNK_PREFIXES:
        if l.startswith(p):
            return True
    for c in JUNK_CONTAINS:
        if c in l:
            return True
    # headings like "[verse 1]" we KEEP
    return False

def strip_leading_junk(lines: List[str]) -> List[str]:
    """Drop leading junk until we hit something that looks like a lyric."""
    out = []
    dropping = True
    for line in lines:
        if dropping:
            # if looks like lyrics, stop dropping
            clean = line.strip()
            if clean and not is_junk_line(clean):
                dropping = False
                out.append(line)
            else:
                # keep nothing
                continue
        else:
            out.append(line)
    return out

def normalize_spanish_textisms(line: str) -> str:
    """
    Fix common SMS shortenings in Spanish lyrics.
    We’ll keep it gentle so we don’t over-correct.
    - ' q ' -> ' que '
    - starting 'q ' -> 'que '
    - line == 'q' -> 'que'
    """
    orig = line

    # whole-line 'q'
    if line.strip().lower() == "q":
      return re.sub(r"^q$", "que", line, flags=re.IGNORECASE)

    # word-boundary q -> que
    # use regex to catch start, middle, end
    def repl(m):
        return m.group(1) + "que" + m.group(2)

    line = re.sub(r'(^|\s)q(\s|$)', repl, line, flags=re.IGNORECASE)

    # maybe someone wrote 'd q' for 'de que' (leave for now — too aggressive)
    return line

def clean_lyrics_block(raw: str) -> str:
    lines = raw.splitlines()
    # strip leading/trailing whitespace
    lines = [l.rstrip() for l in lines]
    # drop obvious site junk at the TOP only
    lines = strip_leading_junk(lines)
    # normalize textisms
    lines = [normalize_spanish_textisms(l) for l in lines]
    # drop trailing extra blank lines
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()

def score_lyrics(text: str) -> Tuple[int, int]:
    """
    Higher is better.
    return (non_empty_lines, total_chars)
    """
    lines = [l for l in text.splitlines() if l.strip()]
    return (len(lines), len(text))

# ------------------------------------------------------------
# 2. source fetchers
#    we’ll make lightweight ones and try to reuse your existing
#    lyrics_fetcher.py if present.
# ------------------------------------------------------------
def fetch_via_existing_script(artist: str, title: str) -> Optional[str]:
    """
    Try to call your existing scripts/lyrics_fetcher.py and capture stdout.
    We call it in "print to stdout" mode (no -o) if supported.
    If that script doesn't support that, we just return None.
    """
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "lyrics_fetcher.py"),
        "lyrics_fetcher.py",
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            import subprocess, tempfile
            try:
                # write to temp file using -o (we know your current script supports -o)
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
                tmp.close()
                cmd = [sys.executable, path, artist, title, "-o", tmp.name]
                subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with open(tmp.name, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                return None
    return None

def fetch_dummy_letras(artist: str, title: str) -> Optional[str]:
    """
    Placeholder for letras.com / musica.com / worship sites.
    For now return None — user can fill this in.
    """
    return None

def fetch_dummy_musixmatch(artist: str, title: str) -> Optional[str]:
    return None

# put all sources here, in order of preference
SOURCE_FUNCS: List[Callable[[str, str], Optional[str]]] = [
    fetch_via_existing_script,
    fetch_dummy_letras,
    fetch_dummy_musixmatch,
]

# ------------------------------------------------------------
# 3. main logic
# ------------------------------------------------------------
def get_best_lyrics(artist: str, title: str) -> str:
    candidates = []
    for fetch in SOURCE_FUNCS:
        try:
            raw = fetch(artist, title)
        except Exception:
            raw = None
        if not raw:
            continue
        cleaned = clean_lyrics_block(raw)
        score = score_lyrics(cleaned)
        candidates.append((score, cleaned))

    if not candidates:
        # fallback placeholder
        return f"{artist} - {title}\n[lyrics not found]\n"

    # sort by score desc
    candidates.sort(key=lambda x: x[0], reverse=True)
    # return top one
    return candidates[0][1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("-o", "--output", help="Where to save the lyrics")
    args = ap.parse_args()

    artist = args.artist
    title = args.title

    best = get_best_lyrics(artist, title)

    if args.output:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(best)
        print(f"[OK] Smart lyrics saved to {args.output}")
    else:
        # print to stdout
        print(best)

if __name__ == "__main__":
    main()
# end of lyrics_fetcher_smart.py
