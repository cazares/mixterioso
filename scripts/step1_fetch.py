#!/usr/bin/env python3
"""Step 1: Fetch assets.

Outputs (best-effort):
- txts/<slug>.txt           (plain lyrics)
- timings/<slug>.lrc        (synced lyrics)
- mp3s/<slug>.mp3           (audio)
- timings/<slug>.<lang>.vtt (captions, last resort)

Notes:
- Uses LRCLIB for lyrics.
- Uses yt-dlp for YouTube search + download.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .common import (
    IOFlags,
    Paths,
    log,
    run_cmd,
    should_write,
    write_json,
    write_text,
    RED,
    YELLOW,
)

# ─────────────────────────────────────────────
# Constants (tuned for speed vs reliability)
# ─────────────────────────────────────────────

YT_SEARCH_LIMIT = 10          # per query
YT_MAX_CANDIDATES = 12        # early exit threshold
YT_SEARCH_TIMEOUT = 12        # seconds
YT_SOCKET_TIMEOUT = "8"

# ─────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────

@dataclass
class YTEntry:
    video_id: str
    title: str
    duration: Optional[float]
    view_count: int

# ─────────────────────────────────────────────
# Lyrics
# ─────────────────────────────────────────────

def fetch_lrclib(query: str) -> Dict[str, Any]:
    try:
        import requests
    except Exception as e:
        log("LYR", f"requests not available: {e}", YELLOW)
        return {}

    r = requests.get(
        "https://lrclib.net/api/search",
        params={"q": query},
        timeout=15,
    )
    r.raise_for_status()
    hits = r.json() or []
    if not hits:
        return {}

    def score(h: Dict[str, Any]) -> Tuple[int, int]:
        synced = 1 if (h.get("syncedLyrics") or "").strip() else 0
        plain = 1 if (h.get("plainLyrics") or "").strip() else 0
        length = len(h.get("syncedLyrics") or h.get("plainLyrics") or "")
        return (synced * 10 + plain, length)

    return sorted(hits, key=score, reverse=True)[0]


def _plain_from_synced_lrc(synced: str) -> str:
    out: List[str] = []
    for raw in synced.splitlines():
        s = raw.strip()
        if not s:
            continue
        s = re.sub(r"^(\[[0-9:.]+\])+\s*", "", s).strip()
        if s:
            out.append(s)
    return "\n".join(out).strip()


def detect_lang_en_es(text: str) -> str:
    """Heuristic language detector: returns "en" or "es" (only these two)."""
    t = (text or "").lower()
    if not t.strip():
        return "en"

    # Strong Spanish signals
    spanish_chars = sum(t.count(ch) for ch in "áéíóúüñ¿¡")
    if spanish_chars >= 2:
        return "es"

    # Tokenize (keep accents)
    tokens = re.findall(r"[a-záéíóúüñ]+", t)
    if not tokens:
        return "en"

    en_sw = {
        "the","and","or","of","to","in","on","for","with","without","as",
        "i","you","he","she","we","they","me","my","your","our","their",
        "is","are","was","were","be","been","being",
        "do","does","did","dont","can't","cant","won't","wont","not","no","yes","but",
        "because","when","where","what","how",
        "this","that","these","those","it","im","i've","ive","i'll","ill","you're","youre","we're","were","isn't","isnt","aren't","arent",
        "love","know","just","all","so",
    }

    es_sw = {
        "el","la","los","las","un","una","unos","unas",
        "y","o","de","del","que","en","por","para","con","sin","como",
        "yo","tu","tú","usted","ustedes","nosotros","vosotros","ellos","ellas",
        "mi","mis","te","se","lo","le","me","pero","si","sí","ya","no",
        "porque","cuando","donde","dónde","qué","como","cómo","mas","más","muy","tambien","también",
        "ser","estar","soy","eres","es","somos","son","estoy","esta","está","estan","están",
        "amor","saber","solo","sólo","todo","toda","todos","todas",
    }

    en_hits = sum(1 for w in tokens if w in en_sw)
    es_hits = sum(1 for w in tokens if w in es_sw)

    es_score = es_hits + (1 if "¿" in t or "¡" in t else 0) + min(spanish_chars, 5)
    en_score = en_hits

    return "es" if es_score >= en_score else "en"

# ─────────────────────────────────────────────
# YouTube search (FAST)
# ─────────────────────────────────────────────

def youtube_search(artist: str, title: str, *, lang_hint: Optional[str] = None) -> List[YTEntry]:
    """Fast, flat YouTube search with early exit."""

    # Prefer language-specific intent first, but keep both in the pool
    if lang_hint == "es":
        queries = [
            f"{artist} {title} letra",
            f"{title} letra",
            f"{artist} {title} karaoke",
            f"{artist} {title} lyrics",
            f"{title} lyrics",
            f"{artist} {title}",
        ]
    else:  # default to English
        queries = [
            f"{artist} {title} lyrics",
            f"{title} lyrics",
            f"{artist} {title} karaoke",
            f"{artist} {title} letra",
            f"{title} letra",
            f"{artist} {title}",
        ]

    seen: set[str] = set()
    entries: List[YTEntry] = []

    for q_raw in queries:
        if len(entries) >= YT_MAX_CANDIDATES:
            break

        q = f"ytsearch{YT_SEARCH_LIMIT}:{q_raw}"
        log("YT", f"Searching YouTube (flat): {q}")

        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            "--force-ipv4",
            "--socket-timeout", YT_SOCKET_TIMEOUT,
            q,
        ]

        try:
            out = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=YT_SEARCH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            log("YT", "yt-dlp flat search timed out; continuing", YELLOW)
            continue
        except Exception as e:
            log("YT", f"yt-dlp flat search failed: {e}", YELLOW)
            continue

        for line in out.splitlines():
            try:
                j = json.loads(line)
            except Exception:
                continue

            vid = (j.get("id") or "").strip()
            if not vid or vid in seen:
                continue
            seen.add(vid)

            yt_title = (j.get("title") or "").strip()
            title_l = yt_title.lower()

            if "official music video" in title_l or "official video" in title_l or "live" in title_l or "official" in title_l:
                continue

            dur = j.get("duration")
            duration = float(dur) if isinstance(dur, (int, float)) else None

            vc = j.get("view_count")
            view_count = int(vc) if isinstance(vc, (int, float)) else 0

            uploader = (j.get("uploader") or "").lower()
            if "lyrics" in uploader or "karaoke" in uploader or "topic" in uploader:
                view_count *= 3

            entries.append(
                YTEntry(
                    video_id=vid,
                    title=yt_title,
                    duration=duration,
                    view_count=view_count,
                )
            )

    return entries

# ─────────────────────────────────────────────
# YouTube selection
# ─────────────────────────────────────────────

def pick_youtube(candidates: List[YTEntry]) -> Optional[YTEntry]:
    if not candidates:
        return None

    buckets: Dict[int, List[YTEntry]] = {}
    for e in candidates:
        if e.duration is None:
            continue
        k = int(round(e.duration / 2) * 2)  # ~2s tolerance
        buckets.setdefault(k, []).append(e)

    if buckets:
        best_k = max(
            buckets.keys(),
            key=lambda k: (len(buckets[k]), sum(x.view_count for x in buckets[k])),
        )
        return max(buckets[best_k], key=lambda x: x.view_count)

    return max(candidates, key=lambda x: x.view_count)

# ─────────────────────────────────────────────
# Downloads
# ─────────────────────────────────────────────

def download_mp3(entry: YTEntry, paths: Paths, *, slug: str, flags: IOFlags) -> bool:
    mp3_path = paths.mp3s / f"{slug}.mp3"
    if mp3_path.exists() and not should_write(mp3_path, flags, label="audio_mp3"):
        log("AUDIO", f"Reusing MP3: {mp3_path}")
        return True

    outtmpl = str((paths.mp3s / slug).with_suffix(".%(ext)s"))
    url = f"https://www.youtube.com/watch?v={entry.video_id}"

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--force-ipv4",
        "--retries", "10",
        "--fragment-retries", "10",
        "--user-agent", "Mozilla/5.0",
        "-o", outtmpl,
        url,
    ]

    rc = run_cmd(cmd, tag="AUDIO", dry_run=flags.dry_run)
    return rc == 0 and mp3_path.exists() or flags.dry_run


def fetch_captions(entry: YTEntry, paths: Paths, *, slug: str, flags: IOFlags, lang_hint: Optional[str] = None) -> bool:
    outtmpl = str((paths.timings / slug).with_suffix(".%(language)s.vtt"))
    url = f"https://www.youtube.com/watch?v={entry.video_id}"

    # Prefer the detected language first, but allow fallback
    sub_langs = "en.*,es.*,.*"
    if lang_hint == "es":
        sub_langs = "es.*,en.*,.*"

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", sub_langs,
        "--sub-format", "vtt",
        "--force-ipv4",
        "--retries", "10",
        "-o", outtmpl,
        url,
    ]

    rc = run_cmd(cmd, tag="CAPT", dry_run=flags.dry_run)
    return rc == 0 and bool(list(paths.timings.glob(f"{slug}*.vtt"))) or flags.dry_run

# ─────────────────────────────────────────────
# Step 1 orchestration
# ─────────────────────────────────────────────

def step1_fetch(
    paths: Paths,
    *,
    query: str,
    artist: str,
    title: str,
    slug: str,
    flags: IOFlags,
) -> Dict[str, Any]:
    paths.ensure()

    txt_path = paths.txts / f"{slug}.txt"
    lrc_path = paths.timings / f"{slug}.lrc"
    mp3_path = paths.mp3s / f"{slug}.mp3"

    summary: Dict[str, Any] = {
        "slug": slug,
        "artist": artist,
        "title": title,
        "query": query,
        "lyrics_source": "none",
        "audio_source": "reuse" if mp3_path.exists() else "none",
        "captions_source": "none",
        "youtube_picked": None,
        "lang": None,
    }

    # ── Lyrics ────────────────────────────────

    try:
        hit = fetch_lrclib(query)
    except Exception as e:
        hit = {}
        log("LYR", f"LRCLIB error: {e}", YELLOW)

    plain = (hit.get("plainLyrics") or "").strip()
    synced = (hit.get("syncedLyrics") or "").strip()

    if not plain and synced:
        plain = _plain_from_synced_lrc(synced)

    # Detect language from best available text (only en/es)
    lang = detect_lang_en_es(plain or synced)
    summary["lang"] = lang

    write_text(txt_path, (plain + "\n") if plain else "", flags, label="lyrics_txt")
    if synced:
        write_text(lrc_path, synced.rstrip() + "\n", flags, label="lyrics_lrc")
        summary["lyrics_source"] = "lrclib_synced"
    elif plain:
        summary["lyrics_source"] = "lrclib_plain"

    # ── YouTube (single pass) ─────────────────

    need_audio = (not mp3_path.exists()) or should_write(mp3_path, flags, label="audio_mp3")
    need_captions = not synced and not any(paths.timings.glob(f"{slug}*.vtt"))

    picked: Optional[YTEntry] = None
    candidates: List[YTEntry] = []

    if need_audio or need_captions:
        candidates = youtube_search(artist, title, lang_hint=lang)
        if candidates:
            top = sorted(candidates, key=lambda e: e.view_count, reverse=True)
            log("YT", "Top candidates (weighted):")
            for i, e in enumerate(top[:10], 1):
                dur = f"{int(round(e.duration))}s" if e.duration else "?"
                log("YT", f"  {i}. {e.view_count:,}  {dur:>6}  {e.title[:80]}")
            picked = pick_youtube(candidates)

    if picked:
        summary["youtube_picked"] = {
            "id": picked.video_id,
            "title": picked.title,
            "duration": picked.duration,
            "views": picked.view_count,
        }

    # ── Audio ────────────────────────────────

    if need_audio:
        if not picked:
            log("AUDIO", "No YouTube candidate selected; cannot download MP3", RED)
        elif download_mp3(picked, paths, slug=slug, flags=flags):
            summary["audio_source"] = "youtube"
    else:
        summary["audio_source"] = "reuse"

    # ── Captions ─────────────────────────────

    if need_captions:
        if not picked:
            log("CAPT", "No YouTube candidate selected; cannot fetch captions", YELLOW)
        elif fetch_captions(picked, paths, slug=slug, flags=flags, lang_hint=lang):
            summary["captions_source"] = "youtube_vtt"

    # ── Meta ─────────────────────────────────

    write_json(paths.meta / f"{slug}.step1.json", summary, flags, label="meta_step1")
    return summary


# end of step1_fetch.py
