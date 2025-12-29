#!/usr/bin/env python3
"""Step 1: Fetch assets.

Outputs (best-effort):
- txts/<slug>.txt          (plain lyrics)
- timings/<slug>.lrc       (synced lyrics)
- mp3s/<slug>.mp3          (audio)
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

from .common import IOFlags, Paths, log, run_cmd, should_write, write_json, write_text, RED, YELLOW


@dataclass
class YTEntry:
    video_id: str
    title: str
    duration: Optional[float]
    view_count: int


def fetch_lrclib(query: str) -> Dict[str, Any]:
    try:
        import requests
    except Exception as e:
        log("LYR", f"requests not available: {e}", YELLOW)
        return {}

    url = "https://lrclib.net/api/search"
    r = requests.get(url, params={"q": query}, timeout=15)
    r.raise_for_status()
    hits = r.json() or []
    if not hits:
        return {}

    def score(h: Dict[str, Any]) -> Tuple[int, int]:
        synced = 1 if (h.get("syncedLyrics") or "").strip() else 0
        plain = 1 if (h.get("plainLyrics") or "").strip() else 0
        length = len((h.get("syncedLyrics") or h.get("plainLyrics") or ""))
        return (synced * 10 + plain, length)

    return sorted(hits, key=score, reverse=True)[0]


def _plain_from_synced_lrc(synced: str) -> str:
    out_lines: List[str] = []
    for raw in synced.splitlines():
        s = raw.strip()
        if not s:
            continue
        s = re.sub(r"^(\[[0-9:.]+\])+\s*", "", s).strip()
        if s:
            out_lines.append(s)
    return "\n".join(out_lines).strip()


def youtube_search(artist: str, title: str, limit: int = 25) -> List[YTEntry]:
    """Fast, reliable YouTube search using flat-playlist (no page resolution)."""

    queries = [
        f"{artist} {title} lyrics",
        f"{artist} {title} karaoke",
        f"{artist} {title}",
        f"{title} lyrics",
    ]

    seen: set[str] = set()
    entries: List[YTEntry] = []

    for q_raw in queries:
        q = f"ytsearch{limit}:{q_raw}"
        log("YT", f"Searching YouTube (flat): {q}")

        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            "--force-ipv4",
            "--socket-timeout", "10",
            q,
        ]

        try:
            out = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15,
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

            # Filter obvious official music videos
            if any(k in title_l for k in ("official music video", "official video")):
                continue

            dur = j.get("duration")
            duration = float(dur) if isinstance(dur, (int, float)) else None

            vc = j.get("view_count")
            view_count = int(vc) if isinstance(vc, (int, float)) else 0

            uploader = (j.get("uploader") or "").lower()
            if any(k in uploader for k in ("lyrics", "karaoke", "topic")):
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


def pick_youtube(candidates: List[YTEntry]) -> Optional[YTEntry]:
    if not candidates:
        return None

    buckets: Dict[int, List[YTEntry]] = {}
    for e in candidates:
        if e.duration is None:
            continue
        k = int(round(e.duration / 2) * 2)
        buckets.setdefault(k, []).append(e)

    if buckets:
        best_k = sorted(
            buckets.keys(),
            key=lambda k: (len(buckets[k]), sum(x.view_count for x in buckets[k])),
            reverse=True,
        )[0]
        return sorted(buckets[best_k], key=lambda x: x.view_count, reverse=True)[0]

    return sorted(candidates, key=lambda x: x.view_count, reverse=True)[0]


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


def fetch_captions(entry: YTEntry, paths: Paths, *, slug: str, flags: IOFlags) -> bool:
    outtmpl = str((paths.timings / slug).with_suffix(".%(language)s.vtt"))
    url = f"https://www.youtube.com/watch?v={entry.video_id}"
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*,es.*,.*",
        "--sub-format", "vtt",
        "--force-ipv4",
        "--retries", "10",
        "-o", outtmpl,
        url,
    ]
    rc = run_cmd(cmd, tag="CAPT", dry_run=flags.dry_run)
    return rc == 0 and (len(list(paths.timings.glob(f"{slug}*.vtt"))) > 0 or flags.dry_run)


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
    }

    try:
        hit = fetch_lrclib(query)
    except Exception as e:
        hit = {}
        log("LYR", f"LRCLIB error: {e}", YELLOW)

    plain = (hit.get("plainLyrics") or "").strip()
    synced = (hit.get("syncedLyrics") or "").strip()

    if (not plain) and synced:
        plain = _plain_from_synced_lrc(synced)

    if plain:
        write_text(txt_path, plain + "\n", flags, label="lyrics_txt")
        summary["lyrics_source"] = "lrclib_plain"
    else:
        write_text(txt_path, "", flags, label="lyrics_txt")

    if synced:
        write_text(
            lrc_path,
            synced + ("\n" if not synced.endswith("\n") else ""),
            flags,
            label="lyrics_lrc",
        )
        summary["lyrics_source"] = "lrclib_synced"

    picked: Optional[YTEntry] = None
    candidates: List[YTEntry] = []

    need_audio = (not mp3_path.exists()) or should_write(mp3_path, flags, label="audio_mp3")
    need_captions = (not synced) and (len(list(paths.timings.glob(f"{slug}*.vtt"))) == 0)

    if need_audio or need_captions:
        candidates = youtube_search(artist, title, limit=25)
        if candidates:
            top = sorted(candidates, key=lambda e: e.view_count, reverse=True)
            log("YT", "Top candidates (sorted by weighted views):")
            for i, e in enumerate(top[:10], 1):
                dur = f"{int(round(e.duration))}s" if e.duration is not None else "?"
                log("YT", f"  {i}. {e.view_count:,}  {dur:>6}  {e.title[:80]}")
            picked = pick_youtube(candidates)

    if picked is not None:
        summary["youtube_picked"] = {
            "id": picked.video_id,
            "title": picked.title,
            "duration": picked.duration,
            "views": picked.view_count,
        }

    if need_audio:
        if picked is None:
            log("AUDIO", "No YouTube candidate selected; cannot download MP3", RED)
        else:
            ok = download_mp3(picked, paths, slug=slug, flags=flags)
            if ok:
                summary["audio_source"] = "youtube"
    else:
        summary["audio_source"] = "reuse"

    if need_captions:
        cap_entry = picked
        if cap_entry is None:
            candidates = youtube_search(artist, title, limit=25)
            cap_entry = pick_youtube(candidates) if candidates else None

        if cap_entry is None:
            log("CAPT", "No YouTube candidate selected; cannot fetch captions", YELLOW)
        else:
            ok = fetch_captions(cap_entry, paths, slug=slug, flags=flags)
            if ok:
                summary["captions_source"] = "youtube_vtt"

    meta_path = paths.meta / f"{slug}.step1.json"
    write_json(meta_path, summary, flags, label="meta_step1")

    return summary


# end of step1_fetch.py
