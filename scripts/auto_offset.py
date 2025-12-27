#!/usr/bin/env python3
"""Whisper-based automatic initial offset suggestion.

Scope:
- ONLY runs when --confirm-offset is used (human remains in control)
- Uses the smallest practical slice(s) of audio near the first lyric
- Produces a *suggested starting offset* for the interactive tuner
- Never auto-locks

Model strategy:
- Prefer faster-whisper (tiny, int8) for speed
- Fallback to OpenAI whisper python package if available
- If neither is available, return None

Perceptual adjustment:
Humans need a little lead time to read before singing. After we align lyrics
to the detected vocal start, we shift lyrics earlier by ~0.5–1.0s.
Default is 0.75s, override with env var MIXTERIOSO_READ_LEAD_SECS.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .common import IOFlags, Paths, log, YELLOW, BLUE

DEFAULT_MODEL = os.environ.get("MIXTERIOSO_WHISPER_MODEL", "tiny")
DEFAULT_READ_LEAD_SECS = float(os.environ.get("MIXTERIOSO_READ_LEAD_SECS", "0.75"))

# Slice sizing: keep small, but tolerate a few seconds of initial drift.
PRE_ROLL_SECS = 8.0
POST_ROLL_SECS = 12.0
SLICE_MAX_SECS = 20.0

# Use first line + 1–2 more as confidence only.
MAX_LINE_ATTEMPTS = 3

# Match requirements: we only need to confidently align the first lyric border.
MIN_MATCH_RATIO = 0.55


@dataclass
class Match:
    line_index: int
    lyric_time: float
    lyric_text: str
    detected_time: float  # absolute time in song (seconds)
    offset_estimate: float
    score: float
    engine: str


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(s: str) -> List[str]:
    n = _norm(s)
    return [t for t in n.split(" ") if t]


def _pick_audio(paths: Paths, slug: str) -> Path:
    for p in [paths.mixes / f"{slug}.wav", paths.mixes / f"{slug}.mp3", paths.mp3s / f"{slug}.mp3"]:
        if p.exists():
            return p
    raise FileNotFoundError(f"No audio found for slug={slug} (expected mixes/ or mp3s/)")


def _read_first_lines(csv_path: Path, max_lines: int) -> List[Tuple[int, float, str]]:
    out: List[Tuple[int, float, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                idx = int((row.get("line_index") or "").strip())
            except Exception:
                idx = len(out)
            try:
                t = float((row.get("time_secs") or "").strip())
            except Exception:
                continue
            txt = (row.get("text") or "").strip()
            if not txt:
                continue
            out.append((idx, t, txt))
            if len(out) >= max_lines:
                break
    return out


def _ensure_slice_wav(audio_path: Path, cache_dir: Path, *, start: float, dur: float) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{audio_path.stem}_slice_{int(start*1000)}_{int(dur*1000)}.wav"
    out = cache_dir / key
    if out.exists():
        return out

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (required for auto-offset slicing)")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(audio_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def _transcribe_faster_whisper(wav_path: Path) -> Tuple[str, List[Tuple[str, float]]]:
    """Returns (full_text, word_starts[(word_norm, start_secs)])."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as e:
        raise RuntimeError(f"faster-whisper not available: {e}")

    model = WhisperModel(DEFAULT_MODEL, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(wav_path),
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
    )

    words: List[Tuple[str, float]] = []
    texts: List[str] = []
    for seg in segments:
        if getattr(seg, "text", None):
            texts.append(seg.text)
        for w in getattr(seg, "words", []) or []:
            wtxt = getattr(w, "word", "") or ""
            wstart = getattr(w, "start", None)
            if wstart is None:
                continue
            wn = _norm(wtxt)
            if wn:
                words.append((wn, float(wstart)))

    return " ".join(texts).strip(), words


def _transcribe_openai_whisper(wav_path: Path) -> Tuple[str, List[Tuple[str, float]]]:
    """Fallback: OpenAI whisper python package (segment-level; approximate word starts)."""
    try:
        import whisper  # type: ignore
    except Exception as e:
        raise RuntimeError(f"openai-whisper not available: {e}")

    model = whisper.load_model(DEFAULT_MODEL)
    res = model.transcribe(str(wav_path), fp16=False, verbose=False)
    text = (res.get("text") or "").strip()

    words: List[Tuple[str, float]] = []
    for seg in res.get("segments", []) or []:
        seg_text = (seg.get("text") or "").strip()
        seg_start = float(seg.get("start") or 0.0)
        # Approximate: treat first token as starting at seg_start.
        toks = _tokenize(seg_text)
        if toks:
            words.append((toks[0], seg_start))
    return text, words


def _best_word_sequence_match(lyric_tokens: List[str], words: List[Tuple[str, float]]) -> Tuple[float, float]:
    """Find earliest start time matching the first few lyric tokens.

    Returns (score, start_time_rel). score is in [0,1].
    """
    if not lyric_tokens or not words:
        return 0.0, 0.0

    # Use first 4 tokens as anchor.
    anchor = lyric_tokens[:4]
    if not anchor:
        return 0.0, 0.0

    w_tokens = [w for (w, _t) in words]
    best_score = 0.0
    best_start = 0.0

    # Scan for first anchor token, then check subsequent tokens in order with a small lookahead window.
    for i, (w, t) in enumerate(words):
        if w != anchor[0]:
            continue
        pos = i
        matched = 1
        for k in range(1, len(anchor)):
            found = False
            for j in range(pos + 1, min(pos + 10, len(words))):
                if words[j][0] == anchor[k]:
                    pos = j
                    matched += 1
                    found = True
                    break
            if not found:
                break
        score = matched / float(len(anchor))
        if score > best_score:
            best_score = score
            best_start = float(t)
            if best_score >= 1.0:
                break

    return best_score, best_start


def _best_token_hit(lyric_tokens: List[str], words: List[Tuple[str, float]]) -> Tuple[float, float]:
    """Fallback match: find the first occurrence of any early lyric token in the transcript words.

    Returns (score, start_time_rel). score is in [0,1].
    """
    if not lyric_tokens or not words:
        return 0.0, 0.0

    targets = set(lyric_tokens[:6])
    if not targets:
        return 0.0, 0.0

    for w, t in words:
        if w in targets:
            # If we found the very first token, treat as stronger.
            score = 0.85 if w == lyric_tokens[0] else 0.65
            return score, float(t)

    return 0.0, 0.0


def _fallback_text_ratio(lyric_text: str, transcript_text: str) -> float:
    lt = _norm(lyric_text)
    tt = _norm(transcript_text)
    if not lt or not tt:
        return 0.0
    # Simple containment heuristic
    if lt in tt:
        return 1.0
    # token overlap ratio
    lset = set(_tokenize(lt))
    tset = set(_tokenize(tt))
    if not lset or not tset:
        return 0.0
    return len(lset & tset) / float(len(lset))


def suggest_initial_offset(
    *,
    paths: Paths,
    slug: str,
    base_offset: float,
    flags: IOFlags,
) -> Optional[float]:
    """Return a suggested initial offset in seconds, or None if unavailable/low confidence."""
    csv_path = paths.timings / f"{slug}.csv"
    if not csv_path.exists():
        log("AUTO_OFFSET", f"Missing timings CSV: {csv_path}", YELLOW)
        return None

    # Cache file (best-effort)
    cache_path = paths.meta / f"{slug}.auto_offset.json"
    if cache_path.exists() and not flags.force:
        try:
            obj = cache_path.read_text(encoding="utf-8", errors="ignore")
            data = __import__("json").loads(obj)
            off = data.get("suggested_offset", None)
            if isinstance(off, (int, float)):
                log("AUTO_OFFSET", f"Reusing cached suggestion: {float(off):+.2f}s", BLUE)
                return float(off)
        except Exception:
            pass

    lines = _read_first_lines(csv_path, MAX_LINE_ATTEMPTS)
    if not lines:
        log("AUTO_OFFSET", "No usable lyric lines in timings CSV", YELLOW)
        return None

    audio_path = _pick_audio(paths, slug)

    # Slice cache directory
    slice_cache_dir = paths.cache / "auto_offset_slices"

    matches: List[Match] = []

    # Pick transcription engine
    engine = None
    transcribe = None
    try:
        # faster-whisper
        import faster_whisper  # noqa: F401  # type: ignore
        engine = "faster_whisper"
        transcribe = _transcribe_faster_whisper
    except Exception:
        try:
            import whisper  # noqa: F401  # type: ignore
            engine = "openai_whisper"
            transcribe = _transcribe_openai_whisper
        except Exception:
            log("AUTO_OFFSET", "No whisper engine found (install faster-whisper or whisper). Skipping.", YELLOW)
            return None

    # Attempt: first line, then next as needed
    for (line_index, lyric_time, lyric_text) in lines:
        # Center the slice around expected time with base_offset, but allow drift.
        center = max(0.0, lyric_time + float(base_offset))
        start = max(0.0, center - PRE_ROLL_SECS)
        dur = min(SLICE_MAX_SECS, PRE_ROLL_SECS + POST_ROLL_SECS)

        wav = _ensure_slice_wav(audio_path, slice_cache_dir, start=start, dur=dur)

        try:
            transcript_text, words = transcribe(wav)
        except Exception as e:
            log("AUTO_OFFSET", f"Transcribe failed ({engine}): {e}", YELLOW)
            continue

        lyric_tokens = _tokenize(lyric_text)
        lyric_tokens = _tokenize(lyric_text)
        seq_score, seq_t_rel = _best_word_sequence_match(lyric_tokens, words)
        hit_score, hit_t_rel = _best_token_hit(lyric_tokens, words)
        overlap_score = _fallback_text_ratio(lyric_text, transcript_text)
        
        # Pick the best timestamped match (sequence match preferred), gated by overlap to avoid false hits.
        score = 0.0
        t_rel = 0.0
        if seq_score >= hit_score:
            score, t_rel = seq_score, seq_t_rel
        else:
            score, t_rel = hit_score, hit_t_rel
        
        if score < MIN_MATCH_RATIO or overlap_score < 0.20:
            log("AUTO_OFFSET", f"Line {line_index}: no confident match (score={score:.2f}, overlap={overlap_score:.2f})", YELLOW)
            continue
        
        detected_abs = start + t_rel
        # perceptual lead: show lyrics slightly earlier than vocal start
        lead = float(DEFAULT_READ_LEAD_SECS)
        offset_est = (detected_abs - lead) - lyric_time

        matches.append(
            Match(
                line_index=line_index,
                lyric_time=lyric_time,
                lyric_text=lyric_text,
                detected_time=detected_abs,
                offset_estimate=offset_est,
                score=score,
                engine=engine,
            )
        )
        log("AUTO_OFFSET", f"Matched line {line_index} score={score:.2f} -> offset {offset_est:+.2f}s (lead={lead:.2f}s)", BLUE)

        # We only need 1–2 good samples.
        if len(matches) >= 2:
            break

    if not matches:
        return None

    offsets = [m.offset_estimate for m in matches]
    suggested = statistics.median(offsets)

    # Confidence: if multiple, ensure they agree roughly.
    if len(offsets) >= 2:
        spread = max(offsets) - min(offsets)
        if spread > 1.25:
            log("AUTO_OFFSET", f"Low confidence: spread={spread:.2f}s, using base offset instead", YELLOW)
            return None

    # Clamp to sane bounds (avoid wild jumps)
    if suggested < -12.0 or suggested > 12.0:
        log("AUTO_OFFSET", f"Suggested offset out of bounds: {suggested:+.2f}s", YELLOW)
        return None

    # Persist cache (best-effort)
    if not flags.dry_run:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "slug": slug,
                "engine": matches[0].engine,
                "model": DEFAULT_MODEL,
                "read_lead_secs": DEFAULT_READ_LEAD_SECS,
                "base_offset": float(base_offset),
                "suggested_offset": float(suggested),
                "matches": [
                    {
                        "line_index": m.line_index,
                        "lyric_time": m.lyric_time,
                        "detected_time": m.detected_time,
                        "offset_estimate": m.offset_estimate,
                        "score": m.score,
                    }
                    for m in matches
                ],
            }
            cache_path.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    log("AUTO_OFFSET", f"Suggested initial offset: {suggested:+.2f}s (from {len(matches)} match(es))", BLUE)
    return float(suggested)

# end of auto_offset.py
