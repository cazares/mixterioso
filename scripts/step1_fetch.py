#!/usr/bin/env python3
"""Step 1: Fetch assets (FAST + cached + parallel).

Primary outputs (best-effort):
- timings/<slug>.lrc        (synced lyrics from LRCLIB)
- mp3s/<slug>.mp3           (audio)

Optional (fallback):
- timings/<slug>.<lang>.vtt (captions, last resort)

Design goals:
- Use YouTube Data API v3 for search + metadata when YOUTUBE_API_KEY is available (faster/consistent).
- Present an interactive menu for top 10 YouTube candidates on first run.
- Cache the chosen YouTube video per query; reuse on subsequent runs unless --force.
- Fetch LRC and MP3 in parallel (and prefetch MP3 while the menu is on-screen).
- Start Demucs immediately after MP3 is available (async), so later steps can reuse stems.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (
    IOFlags,
    Paths,
    log,
    run_cmd,
    should_write,
    write_json,
    write_text,
    have_exe,
    RED,
    YELLOW,
    WHITE,
)

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

YT_MENU_LIMIT = 10
YT_API_TIMEOUT_SECS = 10
YT_DLP_SEARCH_TIMEOUT_SECS = 12
YT_DLP_SOCKET_TIMEOUT = "8"

# Download tuning (speed vs reliability)
YT_DLP_RETRIES = "10"
YT_DLP_FRAGMENT_RETRIES = "10"
YT_DLP_CONCURRENT_FRAGMENTS = "4"

LRCLIB_TIMEOUT_SECS = 15

# ─────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class YTCandidate:
    video_id: str
    title: str
    channel: str
    duration_secs: Optional[float]
    view_count: int


@dataclass
class Step1Result:
    summary: Dict[str, Any]
    demucs_proc: Optional[subprocess.Popen] = None


# ─────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def _norm_query(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip())


def _get_youtube_api_key() -> Optional[str]:
    for k in ("YOUTUBE_API_KEY", "YOUTUBE_DATA_API_KEY", "YT_API_KEY"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return None


def _yt_cache_dir(paths: Paths) -> Path:
    d = paths.meta / "yt_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mp3_cache_dir(paths: Paths) -> Path:
    d = paths.mp3s / ".yt_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lrc_cache_dir(paths: Paths) -> Path:
    d = paths.timings / ".yt_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean_title_for_parse(s: str) -> str:
    t = (s or "").strip()

    # Drop common bracketed noise.
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\([^)]*\)", "", t)

    # Normalize separators.
    t = t.replace("—", "-").replace("–", "-")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _strip_feature_noise(s: str) -> str:
    t = (s or "").strip()
    # Remove obvious trailing keywords; keep the core song title.
    t = re.sub(r"\b(official\s+music\s+video|official\s+video|lyrics|lyric\s+video|audio|karaoke|visualizer)\b.*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s+", " ", t).strip(" -|")
    return t.strip()


def _guess_artist_title(yt_title: str, yt_channel: str) -> Tuple[str, str]:
    """Best-effort parse 'Artist - Title' from YouTube metadata."""
    raw = _strip_feature_noise(_clean_title_for_parse(yt_title))
    if " - " in raw:
        a, b = [x.strip() for x in raw.split(" - ", 1)]
        if a and b:
            return a, b

    # Channel often looks like "Artist - Topic"
    ch = (yt_channel or "").strip()
    ch = re.sub(r"\s*-\s*Topic\s*$", "", ch, flags=re.I).strip()
    if ch:
        return ch, raw or ch
    return "Unknown", raw or "Unknown"


def _parse_iso8601_duration(dur: str) -> Optional[float]:
    # e.g., PT3M41S, PT1H2M3S
    if not dur:
        return None
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return float(h * 3600 + mi * 60 + s)


# ─────────────────────────────────────────────
# LRCLIB (synced lyrics)
# ─────────────────────────────────────────────

def fetch_lrclib_best_synced(query: str) -> Dict[str, Any]:
    try:
        import requests
    except Exception as e:
        log("LRC", f"requests not available: {e}", YELLOW)
        return {}

    q = _norm_query(query)
    r = requests.get(
        "https://lrclib.net/api/search",
        params={"q": q},
        timeout=LRCLIB_TIMEOUT_SECS,
    )
    r.raise_for_status()
    hits = r.json() or []
    if not hits:
        return {}

    def score(h: Dict[str, Any]) -> Tuple[int, int]:
        synced = 1 if (h.get("syncedLyrics") or "").strip() else 0
        plain = 1 if (h.get("plainLyrics") or "").strip() else 0
        length = len((h.get("syncedLyrics") or h.get("plainLyrics") or "").strip())
        return (synced * 100 + plain * 10, length)

    return sorted(hits, key=score, reverse=True)[0]


def _extract_synced_lrc(hit: Dict[str, Any]) -> str:
    return (hit.get("syncedLyrics") or "").strip()


# ─────────────────────────────────────────────
# YouTube Data API v3
# ─────────────────────────────────────────────

def youtube_api_search_candidates(query: str, *, api_key: str, limit: int = YT_MENU_LIMIT) -> List[YTCandidate]:
    try:
        import requests
    except Exception as e:
        log("YT", f"requests not available: {e}", YELLOW)
        return []

    q = _norm_query(query)
    # Search
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "type": "video",
            "maxResults": str(limit),
            "q": q,
            "key": api_key,
        },
        timeout=YT_API_TIMEOUT_SECS,
    )
    r.raise_for_status()
    data = r.json() or {}
    items = data.get("items") or []
    video_ids = [((it.get("id") or {}).get("videoId") or "").strip() for it in items]
    video_ids = [vid for vid in video_ids if vid]
    if not video_ids:
        return []

    # Details (duration, views, canonical title/channel)
    r2 = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(video_ids),
            "key": api_key,
            "maxResults": str(limit),
        },
        timeout=YT_API_TIMEOUT_SECS,
    )
    r2.raise_for_status()
    data2 = r2.json() or {}
    items2 = data2.get("items") or []
    by_id: Dict[str, Dict[str, Any]] = {((it.get("id") or "").strip()): it for it in items2 if (it.get("id") or "").strip()}

    out: List[YTCandidate] = []
    for vid in video_ids:
        it = by_id.get(vid) or {}
        sn = it.get("snippet") or {}
        cd = it.get("contentDetails") or {}
        st = it.get("statistics") or {}
        title = (sn.get("title") or "").strip()
        channel = (sn.get("channelTitle") or "").strip()
        duration = _parse_iso8601_duration((cd.get("duration") or "").strip())
        try:
            views = int(st.get("viewCount") or 0)
        except Exception:
            views = 0

        # Only filter the phrase the user explicitly called out (keep the rest for user choice).
        if "official music video" in title.lower():
            continue

        out.append(
            YTCandidate(
                video_id=vid,
                title=title,
                channel=channel,
                duration_secs=duration,
                view_count=views,
            )
        )

    return out


# ─────────────────────────────────────────────
# yt-dlp fallback search (when no API key)
# ─────────────────────────────────────────────

def ytdlp_search_candidates(query: str, *, limit: int = YT_MENU_LIMIT) -> List[YTCandidate]:
    q = _norm_query(query)
    search = f"ytsearch{limit}:{q}"
    log("YT", f"Searching YouTube (yt-dlp flat): {search}", WHITE)

    cmd = [
        "yt-dlp",
        "--dump-json",
        "--flat-playlist",
        "--no-warnings",
        "--force-ipv4",
        "--socket-timeout", YT_DLP_SOCKET_TIMEOUT,
        search,
    ]

    try:
        out = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=YT_DLP_SEARCH_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        log("YT", "yt-dlp flat search timed out", YELLOW)
        return []
    except Exception as e:
        log("YT", f"yt-dlp flat search failed: {e}", YELLOW)
        return []

    entries: List[YTCandidate] = []
    for line in out.splitlines():
        try:
            j = json.loads(line)
        except Exception:
            continue
        vid = (j.get("id") or "").strip()
        if not vid:
            continue
        title = (j.get("title") or "").strip()
        channel = (j.get("uploader") or j.get("channel") or "").strip()
        dur = j.get("duration")
        duration = float(dur) if isinstance(dur, (int, float)) else None
        vc = j.get("view_count")
        views = int(vc) if isinstance(vc, (int, float)) else 0

        if "official music video" in title.lower():
            continue

        entries.append(YTCandidate(video_id=vid, title=title, channel=channel, duration_secs=duration, view_count=views))

    return entries


# ─────────────────────────────────────────────
# Interactive selection
# ─────────────────────────────────────────────

def _fmt_duration(secs: Optional[float]) -> str:
    if secs is None:
        return "?"
    s = int(round(float(secs)))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def prompt_pick_youtube(candidates: List[YTCandidate]) -> Optional[YTCandidate]:
    if not candidates:
        return None

    # Sort by views desc as the default order.
    ranked = sorted(candidates, key=lambda e: e.view_count, reverse=True)[:YT_MENU_LIMIT]

    log("YT", "Top YouTube candidates:", WHITE)
    for i, e in enumerate(ranked, 1):
        dur = _fmt_duration(e.duration_secs)
        views = f"{e.view_count:,}" if e.view_count else "?"
        log("YT", f"  {i:>2}. {views:>12}  {dur:>7}  {e.title}  [{e.channel}]", WHITE)

    while True:
        ans = input(f"Pick a video [1-{len(ranked)}] (Enter=1, q=abort): ").strip().lower()
        if ans == "":
            return ranked[0]
        if ans in ("q", "quit", "exit"):
            return None
        try:
            n = int(ans)
            if 1 <= n <= len(ranked):
                return ranked[n - 1]
        except Exception:
            pass
        print("Invalid selection")


# ─────────────────────────────────────────────
# MP3 download prefetch (cancellable)
# ─────────────────────────────────────────────

class Mp3Prefetch:
    def __init__(self, *, paths: Paths, flags: IOFlags):
        self.paths = paths
        self.flags = flags
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._video_id: Optional[str] = None

    def cache_path(self, video_id: str) -> Path:
        return _mp3_cache_dir(self.paths) / f"{video_id}.mp3"

    def is_cached(self, video_id: str) -> bool:
        p = self.cache_path(video_id)
        return p.exists() and p.stat().st_size > 0

    def start(self, video_id: str) -> None:
        with self._lock:
            if self._video_id == video_id and self._proc and self._proc.poll() is None:
                return
            self.stop()

            if self.flags.dry_run:
                self._video_id = video_id
                self._proc = None
                return

            out_mp3 = self.cache_path(video_id)
            if out_mp3.exists() and not self.flags.force:
                self._video_id = video_id
                self._proc = None
                return

            if not have_exe("yt-dlp"):
                log("AUDIO", "yt-dlp not found on PATH; cannot download MP3", RED)
                self._video_id = video_id
                self._proc = None
                return

            url = f"https://www.youtube.com/watch?v={video_id}"
            outtmpl = str((_mp3_cache_dir(self.paths) / video_id).with_suffix(".%(ext)s"))

            cmd = [
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "--force-ipv4",
                "--concurrent-fragments", YT_DLP_CONCURRENT_FRAGMENTS,
                "--retries", YT_DLP_RETRIES,
                "--fragment-retries", YT_DLP_FRAGMENT_RETRIES,
                "--user-agent", "Mozilla/5.0",
                "--no-warnings",
                "-o", outtmpl,
                url,
            ]

            log("AUDIO", f"Prefetch MP3 (cache): {video_id}", WHITE)
            self._video_id = video_id
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None
            self._video_id = None

    def wait(self, video_id: str) -> bool:
        with self._lock:
            proc = self._proc
            cur = self._video_id
        if self.flags.dry_run:
            return True
        if self.is_cached(video_id) and not self.flags.force:
            return True
        if cur != video_id:
            # Not the active download; start it now and wait.
            self.start(video_id)
            with self._lock:
                proc = self._proc
                cur = self._video_id
        if proc and cur == video_id:
            rc = proc.wait()
            return rc == 0 and self.is_cached(video_id)
        return self.is_cached(video_id)


# ─────────────────────────────────────────────
# Captions fallback (LAST RESORT)
# ─────────────────────────────────────────────

def fetch_captions_vtt(video_id: str, paths: Paths, *, slug: str, flags: IOFlags) -> bool:
    outtmpl = str((paths.timings / slug).with_suffix(".%(language)s.vtt"))
    url = f"https://www.youtube.com/watch?v={video_id}"

    if not have_exe("yt-dlp"):
        log("CAPT", "yt-dlp not found on PATH; cannot fetch captions", YELLOW)
        return False

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*,es.*,.*",
        "--sub-format", "vtt",
        "--force-ipv4",
        "--retries", YT_DLP_RETRIES,
        "-o", outtmpl,
        url,
    ]

    rc = run_cmd(cmd, tag="CAPT", dry_run=flags.dry_run)
    return (rc == 0 and bool(list(paths.timings.glob(f"{slug}*.vtt")))) or flags.dry_run


# ─────────────────────────────────────────────
# Demucs async kickoff
# ─────────────────────────────────────────────

def start_demucs_async(paths: Paths, *, slug: str, src_mp3: Path, flags: IOFlags) -> Optional[subprocess.Popen]:
    """Kick Demucs in the background if stems aren't already present."""
    if flags.dry_run:
        return None

    stem_dir = paths.separated / "htdemucs" / slug
    have_all = all((stem_dir / f"{name}.wav").exists() for name in ("vocals", "bass", "drums", "other"))
    if have_all and not flags.force:
        return None

    if not have_exe("demucs"):
        log("DEMUCS", "demucs not found on PATH; skipping", YELLOW)
        return None

    paths.separated.mkdir(parents=True, exist_ok=True)

    cmd = [
        "demucs",
        "-n", "htdemucs",
        "--shifts", "1",
        "--overlap", "0.10",
        "-d", "mps",
        "-o", str(paths.separated),
        str(src_mp3),
    ]

    log("DEMUCS", f"Starting Demucs async for {slug}", WHITE)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─────────────────────────────────────────────
# Step 1 orchestration
# ─────────────────────────────────────────────

def step1_fetch(
    paths: Paths,
    *,
    query: str,
    flags: IOFlags,
) -> Step1Result:
    """Resolve YouTube candidate -> derive artist/title/slug -> fetch LRC+MP3 in parallel (cached)."""
    paths.ensure()

    qnorm = _norm_query(query)
    api_key = _get_youtube_api_key()

    yt_cache = _yt_cache_dir(paths)
    qcache_path = yt_cache / f"query_{_sha1(qnorm)}.json"

    picked: Optional[YTCandidate] = None
    cached: Dict[str, Any] = {}
    if qcache_path.exists() and not flags.force:
        try:
            cached = json.loads(qcache_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}

    # If cached selection exists, reuse it without prompting.
    if cached.get("video_id") and not flags.force:
        vid = str(cached.get("video_id")).strip()
        if vid:
            log("YT", f"Using cached YouTube pick for query: {vid} (use --force to re-pick)", WHITE)
            # Minimal metadata; we will still populate from cache for slug/artist/title if present.
            picked = YTCandidate(
                video_id=vid,
                title=str(cached.get("yt_title") or ""),
                channel=str(cached.get("yt_channel") or ""),
                duration_secs=float(cached.get("duration_secs") or 0.0) if cached.get("duration_secs") is not None else None,
                view_count=int(cached.get("view_count") or 0),
            )

    # Search + prompt when not cached.
    candidates: List[YTCandidate] = []
    if picked is None:
        try:
            if api_key:
                candidates = youtube_api_search_candidates(qnorm, api_key=api_key, limit=YT_MENU_LIMIT)
            else:
                candidates = ytdlp_search_candidates(qnorm, limit=YT_MENU_LIMIT)
        except Exception as e:
            log("YT", f"YouTube search failed: {e}", YELLOW)
            candidates = []

        if not candidates:
            log("YT", "No YouTube candidates found", RED)
            picked = None
        else:
            # Start MP3 prefetch for default candidate while the menu is displayed.
            prefetch = Mp3Prefetch(paths=paths, flags=flags)
            prefetch.start(sorted(candidates, key=lambda e: e.view_count, reverse=True)[0].video_id)

            picked = prompt_pick_youtube(candidates)
            if picked is None:
                prefetch.stop()
                return Step1Result(
                    summary={
                        "query": qnorm,
                        "error": "user_aborted_youtube_pick",
                    },
                    demucs_proc=None,
                )

            # Switch prefetch to the chosen candidate and keep it running.
            prefetch.start(picked.video_id)
    else:
        prefetch = Mp3Prefetch(paths=paths, flags=flags)
        # For cached picks, start prefetch immediately for speed.
        prefetch.start(picked.video_id)

    # Resolve artist/title/slug from YouTube metadata.
    yt_title = (picked.title or "").strip()
    yt_channel = (picked.channel or "").strip()
    artist, title = _guess_artist_title(yt_title, yt_channel)

    # Import slugify lazily (common) to keep step1_fetch self-contained.
    from .common import slugify  # local import

    slug = slugify(title)

    # Paths
    lrc_path = paths.timings / f"{slug}.lrc"
    mp3_path = paths.mp3s / f"{slug}.mp3"

    # Cache files keyed by video id
    lrc_cache_path = _lrc_cache_dir(paths) / f"{picked.video_id}.lrc"
    mp3_cache_path = prefetch.cache_path(picked.video_id)

    summary: Dict[str, Any] = {
        "query": qnorm,
        "artist": artist,
        "title": title,
        "slug": slug,
        "youtube": {
            "video_id": picked.video_id,
            "yt_title": yt_title,
            "yt_channel": yt_channel,
            "duration_secs": picked.duration_secs,
            "view_count": picked.view_count,
            "used_api": bool(api_key),
        },
        "lrc": {"path": str(lrc_path), "source": "none"},
        "mp3": {"path": str(mp3_path), "source": "none"},
        "demucs": {"started": False, "pid": None},
    }

    # Persist query cache for fast reuse
    if not flags.dry_run:
        try:
            qcache_path.write_text(
                json.dumps(
                    {
                        "query": qnorm,
                        "video_id": picked.video_id,
                        "yt_title": yt_title,
                        "yt_channel": yt_channel,
                        "duration_secs": picked.duration_secs,
                        "view_count": picked.view_count,
                        "artist": artist,
                        "title": title,
                        "slug": slug,
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── Ensure MP3 + LRC in parallel ────────────────────────────────

    errors: List[str] = []

    def ensure_mp3() -> bool:
        # If final MP3 exists and not forced, skip everything (and don't touch caches).
        if mp3_path.exists() and not flags.force:
            summary["mp3"]["source"] = "reuse"
            return True

        # Wait for cached mp3
        ok = prefetch.wait(picked.video_id)
        if not ok:
            errors.append("mp3_download_failed")
            return False

        if flags.dry_run:
            summary["mp3"]["source"] = "youtube"
            return True

        if not mp3_cache_path.exists():
            errors.append("mp3_cache_missing")
            return False

        # Copy cache -> final (fast local IO)
        mp3_path.parent.mkdir(parents=True, exist_ok=True)
        if not should_write(mp3_path, flags, label="audio_mp3"):
            summary["mp3"]["source"] = "skip_overwrite"
            return mp3_path.exists()
        shutil.copy2(mp3_cache_path, mp3_path)
        summary["mp3"]["source"] = "youtube"
        return mp3_path.exists()

    def ensure_lrc() -> bool:
        # If final LRC exists and not forced, do NOT hit LRCLIB.
        if lrc_path.exists() and not flags.force:
            summary["lrc"]["source"] = "reuse"
            return True

        # If we have a cached LRC (keyed by video id), reuse it.
        if lrc_cache_path.exists() and not flags.force:
            if not flags.dry_run:
                lrc_path.parent.mkdir(parents=True, exist_ok=True)
                lrc_path.write_text(lrc_cache_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            summary["lrc"]["source"] = "cache"
            return True

        # Fetch from LRCLIB. Try a few progressively wider queries, but keep it light.
        queries = [
            f"{artist} {title}",
            qnorm,
            f"{artist} {title} lyrics",
            f"{artist} {title} letra",
        ]

        hit: Dict[str, Any] = {}
        last_err: Optional[str] = None
        for q in queries:
            try:
                hit = fetch_lrclib_best_synced(q)
            except Exception as e:
                hit = {}
                last_err = str(e)
            synced = _extract_synced_lrc(hit)
            if synced:
                if not flags.dry_run:
                    lrc_path.parent.mkdir(parents=True, exist_ok=True)
                    if should_write(lrc_path, flags, label="lyrics_lrc"):
                        write_text(lrc_path, synced + "\n", flags, label="lyrics_lrc")
                    # cache by video_id as well
                    try:
                        lrc_cache_path.write_text(synced + "\n", encoding="utf-8")
                    except Exception:
                        pass
                summary["lrc"]["source"] = "lrclib"
                return True

        if last_err:
            log("LRC", f"LRCLIB error: {last_err}", YELLOW)
        errors.append("lrc_not_found")
        return False

    # Run in parallel
    t_mp3 = threading.Thread(target=ensure_mp3, daemon=True)
    t_lrc = threading.Thread(target=ensure_lrc, daemon=True)
    t_mp3.start()
    t_lrc.start()
    t_mp3.join()
    t_lrc.join()

    # Captions fallback ONLY if LRC missing (keeps API calls minimal).
    if (not lrc_path.exists()) and (not flags.dry_run):
        log("LRC", "No synced LRC found; trying captions as last resort", YELLOW)
        if fetch_captions_vtt(picked.video_id, paths, slug=slug, flags=flags):
            summary["lrc"]["source"] = "missing_used_vtt"

    # ── Start Demucs immediately (async) ────────────────────────────

    demucs_proc: Optional[subprocess.Popen] = None
    if mp3_path.exists() or flags.dry_run:
        try:
            demucs_proc = start_demucs_async(paths, slug=slug, src_mp3=mp3_path, flags=flags)
            if demucs_proc is not None:
                summary["demucs"]["started"] = True
                summary["demucs"]["pid"] = demucs_proc.pid
        except Exception as e:
            log("DEMUCS", f"Failed to start Demucs: {e}", YELLOW)

    if errors:
        summary["errors"] = errors

    # ── Meta ─────────────────────────────────
    write_json(paths.meta / f"{slug}.step1.json", summary, flags, label="meta_step1")
    return Step1Result(summary=summary, demucs_proc=demucs_proc)


# end of step1_fetch.py
