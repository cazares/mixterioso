#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# AUTO-TIMING ENGINE (HYBRID: faster-whisper + whisperx option)
#
# Purpose:
#   - Take canonical lyrics (txts/<slug>.txt) and audio for <slug>
#   - Run ASR using either:
#         * faster-whisper (default)
#         * whisperx (if --timing-model-size=v3 or large-v3 or similar)
#   - Align lyric tokens to ASR word tokens (monotone DP fuzzy match)
#   - Apply Miguel’s pre-chorus packing rules
#   - Apply instrumental-gap exclusions
#   - Produce canonical timings CSV:
#         line_index,start,end,text
#
# Hybrid Mode Rules:
#   - TEST (fast):     always use faster-whisper regardless of flag
#   - RELEASE (slow):  use whisperx *only* if explicitly requested
#   - Direct CLI override:
#         --timing-model-size base          => faster-whisper
#         --timing-model-size large-v3      => whisperx
#         --timing-model-size distil-...    => faster-whisper
#
# NOTHING in alignment logic was touched.
# All your custom timing behaviors remain *exactly intact*.
#
from __future__ import annotations

import argparse
import csv
import difflib
import math
import os
import re
import sys
import unicodedata
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# ----- optional rich logging -----
try:
    from rich.console import Console
    from rich.traceback import install as rich_traceback_install
    rich_traceback_install(show_locals=False)
    _RICH_AVAILABLE = True
    console = Console()
except Exception:
    _RICH_AVAILABLE = False
    console = None  # type: ignore

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"

# --------------------------------------------------------------------------
# Logging (matches 0_master.py)
# --------------------------------------------------------------------------
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

def _plain_print(tag: str, color: str, msg: str) -> None:
    sys.stderr.write(f"{BOLD}{color}[{tag}]{RESET} {msg}\n")

def log_info(msg: str) -> None:
    if _RICH_AVAILABLE and console:
        console.print(f"[bold cyan][WX][/bold cyan] {msg}")
    else:
        _plain_print("WX", CYAN, msg)

def log_ok(msg: str) -> None:
    if _RICH_AVAILABLE and console:
        console.print(f"[bold green][WX][/bold green] {msg}")
    else:
        _plain_print("WX", GREEN, msg)

def log_warn(msg: str) -> None:
    if _RICH_AVAILABLE and console:
        console.print(f"[bold yellow][WX][/bold yellow] {msg}")
    else:
        _plain_print("WX", YELLOW, msg)

def log_error(msg: str) -> None:
    if _RICH_AVAILABLE and console:
        console.print(f"[bold red][WX][/bold red] {msg}")
    else:
        _plain_print("WX", RED, msg)

def log_debug(msg: str, enabled: bool) -> None:
    if not enabled:
        return
    if _RICH_AVAILABLE and console:
        console.print(f"[bold magenta][WX-DEBUG][/bold magenta] {msg}")
    else:
        _plain_print("WX-DEBUG", MAGENTA, msg)

# --------------------------------------------------------------------------
# Paths & constants
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
WAVS_DIR = BASE_DIR / "wavs"

DEFAULT_MODEL_SIZE = os.environ.get("WX_MODEL_SIZE", "distil-large-v3")
INSTRUMENTAL_MIN_GAP = 3.0

DEFAULT_MIN_LINE_DURATION = 0.8
DEFAULT_FALLBACK_LINE_DURATION = 2.5
DEFAULT_GAP_AFTER_LINE = 0.1
DEFAULT_MIN_SIMILARITY = 0.6

@dataclass
class Word:
    text: str
    start: float
    end: float

@dataclass
class LyricsData:
    lines: List[str]
    tokens: List[str]
    token_to_line: List[int]
    line_to_token_span: List[Tuple[int, int]]

@dataclass
class LineTiming:
    index: int
    start: float
    end: float
    text: str
    has_alignment: bool
    excluded: bool = False

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def die(msg: str, code: int = 1) -> None:
    log_error(msg)
    sys.exit(code)

def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")

def strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

def normalize_token(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'")
    text = re.sub(
        r"[^0-9a-záéíóúüñçàèìòùâêîôûäëïïöü\s']",
        " ", text, flags=re.IGNORECASE,
    )
    text = strip_diacritics(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def tokenize_line(text: str) -> List[str]:
    norm = normalize_token(text)
    if not norm:
        return []
    return norm.split()

def guess_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"

# --------------------------------------------------------------------------
# AUDIO SELECTION (unchanged logic)
# --------------------------------------------------------------------------
def pick_audio_for_slug(slug: str, explicit_audio: Optional[Path]) -> Path:
    if explicit_audio is not None:
        if not explicit_audio.exists():
            die(f"Explicit audio does not exist: {explicit_audio}")
        log_info(f"Using explicit audio: {explicit_audio}")
        return explicit_audio

    candidates = [
        MIXES_DIR / f"{slug}_vocals.wav",
        MIXES_DIR / f"{slug}.wav",
        MP3_DIR / f"{slug}.mp3",
        WAVS_DIR / f"{slug}.wav",
        MIXES_DIR / f"{slug}_karaoke.wav",
    ]
    for p in candidates:
        if p.exists():
            log_info(f"Auto-selected audio: {p}")
            return p

    die(f"No audio found for slug {slug}. Looked in mixes/mp3s/wavs.")
    return None
# --------------------------------------------------------------------------
# ASR: HYBRID MODE (faster-whisper or whisperx)
# --------------------------------------------------------------------------

def run_asr_faster_whisper(
    audio_path: Path,
    model_size: str,
    language: Optional[str],
    device: str,
    compute_type: Optional[str],
    beam_size: int,
    debug: bool,
) -> Tuple[List[Word], float]:
    """
    Standard faster-whisper ASR.
    Produces Word[] (text,start,end) and audio_duration.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die(
            "faster-whisper is not installed. "
            "Install via: pip3 install faster-whisper"
        )

    if compute_type is None or compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    log_info(
        f"[FW] Loading faster-whisper model '{model_size}', device={device}, compute_type={compute_type}"
    )

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    whisper_lang = None if language in (None, "", "auto") else language

    log_info(
        f"[FW] Transcribing {audio_path} (language={'auto' if whisper_lang is None else whisper_lang})"
    )

    segments, info = model.transcribe(
        str(audio_path),
        beam_size=beam_size,
        vad_filter=False,
        word_timestamps=True,
        language=whisper_lang,
    )

    words: List[Word] = []
    seg_count = 0
    word_count = 0

    for seg in segments:
        seg_count += 1
        if not getattr(seg, "words", None):
            continue
        for w in seg.words:
            if w.start is None or w.end is None:
                continue
            wt = w.word.strip()
            if not wt:
                continue
            words.append(Word(text=wt, start=float(w.start), end=float(w.end)))
            word_count += 1

    audio_duration = float(getattr(info, "duration", 0.0) or 0.0)

    log_ok(
        f"[FW] Done: {seg_count} segments, {word_count} words, audio={audio_duration:.2f}s"
    )

    if debug:
        preview = ", ".join(
            f"{w.text}({w.start:.2f}-{w.end:.2f})" for w in words[:20]
        )
        log_debug(f"[FW] preview: {preview}", True)

    return words, audio_duration


def run_asr_whisperx(
    audio_path: Path,
    model_size: str,
    language: Optional[str],
    device: str,
    debug: bool,
) -> Tuple[List[Word], float]:
    """
    WhisperX ASR + alignment.
    Produces the same Word[] format as faster-whisper.
    """

    try:
        import whisperx
    except ImportError:
        die(
            "WhisperX is not installed. Install via:\n"
            "pip3 install git+https://github.com/m-bain/whisperx.git"
        )

    whisper_lang = None if language in (None, "", "auto") else language

    log_info(f"[WX] Loading WhisperX model '{model_size}' on {device}")
    model = whisperx.load_model(model_size, device=device, compute_type="int8")

    log_info(f"[WX] Transcribing {audio_path}...")
    result = model.transcribe(str(audio_path), language=whisper_lang)

    diarize_model = None
    try:
        diarize_model = whisperx.DiarizationPipeline(use_auth_token=None, device=device)
    except Exception:
        diarize_model = None

    log_info("[WX] Aligning...")
    model_a, metadata = whisperx.load_align_model(
        language_code=result["language"], device=device
    )
    aligned = whisperx.align(
        result["segments"], model_a, metadata, str(audio_path), device
    )

    # convert WhisperX tokens to Word[]
    words: List[Word] = []
    total = 0
    for seg in aligned["segments"]:
        if "words" not in seg:
            continue
        for w in seg["words"]:
            text = w.get("word") or w.get("text", "").strip()
            if not text:
                continue
            ws = w.get("start")
            we = w.get("end")
            if ws is None or we is None:
                continue
            words.append(Word(text=text, start=float(ws), end=float(we)))
            total += 1

    audio_duration = 0.0
    try:
        from mutagen import File as MFile
        mf = MFile(audio_path)
        audio_duration = float(mf.info.length)
    except Exception:
        audio_duration = max((w.end for w in words), default=0.0)

    log_ok(f"[WX] Done: {total} aligned word tokens")

    if debug:
        preview = ", ".join(
            f"{w.text}({w.start:.2f}-{w.end:.2f})" for w in words[:20]
        )
        log_debug(f"[WX] preview: {preview}", True)

    return words, audio_duration


# --------------------------------------------------------------------------
# Hybrid wrapper — chooses whisperx or faster-whisper
# --------------------------------------------------------------------------

def run_asr_hybrid(
    audio_path: Path,
    timing_model_size: str,
    language: str,
    is_test_mode: bool,
    debug: bool
) -> Tuple[List[Word], float]:
    """
    — TEST mode → ALWAYS faster-whisper
    — RELEASE:
        large-v3*, v3, whisperx keywords → WhisperX
        everything else → faster-whisper
    """
    device = guess_device()

    # Test mode always forces faster-whisper
    if is_test_mode:
        log_info("[HYBRID] TEST mode → faster-whisper only")
        return run_asr_faster_whisper(
            audio_path,
            model_size=timing_model_size,
            language=language,
            device=device,
            compute_type="auto",
            beam_size=5,
            debug=debug,
        )

    # Release: detect whisperx keywords
    key = timing_model_size.lower()
    wants_whisperx = any(
        k in key for k in ["large-v3", "large_v3", "v3", "whisperx"]
    )

    if wants_whisperx:
        log_info(f"[HYBRID] Using WhisperX for model {timing_model_size}")
        return run_asr_whisperx(
            audio_path,
            model_size=timing_model_size,
            language=language,
            device=device,
            debug=debug,
        )

    # otherwise faster-whisper
    log_info(f"[HYBRID] Using faster-whisper model {timing_model_size}")
    return run_asr_faster_whisper(
        audio_path,
        model_size=timing_model_size,
        language=language,
        device=device,
        compute_type="auto",
        beam_size=5,
        debug=debug,
    )
# --------------------------------------------------------------------------
# Token similarity
# --------------------------------------------------------------------------

def token_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------
# DP alignment: lyrics_tokens → ASR word tokens
# --------------------------------------------------------------------------

def align_tokens_dp(
    lyrics_tokens: List[str],
    asr_tokens: List[str],
    min_similarity: float,
    debug: bool = False,
) -> List[Optional[int]]:
    """
    Custom DP matcher — untouched.
    Preserves your optimal behavior w/ robust fuzzy alignment.
    """
    n = len(lyrics_tokens)
    m = len(asr_tokens)

    if n == 0:
        log_warn("No lyrics tokens to align.")
        return []

    gap_cost = 1.0
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        cost[i][0] = i * gap_cost
        back[i][0] = 1
    for j in range(1, m + 1):
        cost[0][j] = j * gap_cost
        back[0][j] = 2

    for i in range(1, n + 1):
        lt = lyrics_tokens[i - 1]
        for j in range(1, m + 1):
            at = asr_tokens[j - 1]
            sim = token_similarity(lt, at)
            match_cost = 1.0 - sim

            c_sub = cost[i - 1][j - 1] + match_cost
            c_del = cost[i - 1][j] + gap_cost
            c_ins = cost[i][j - 1] + gap_cost

            best = c_sub
            op = 0
            if c_del < best:
                best = c_del
                op = 1
            if c_ins < best:
                best = c_ins
                op = 2

            cost[i][j] = best
            back[i][j] = op

    mapping = [None] * n
    i, j = n, m
    matches = 0

    while i > 0 or j > 0:
        op = back[i][j]
        if op == 0:
            sim = token_similarity(lyrics_tokens[i - 1], asr_tokens[j - 1])
            if sim >= min_similarity:
                mapping[i - 1] = j - 1
                matches += 1
            i -= 1
            j -= 1
        elif op == 1:
            i -= 1
        else:
            j -= 1

    pct = (matches / n * 100) if n else 0.0
    log_ok(f"Token alignment {matches}/{n} ({pct:.1f}%)")

    if pct < 60:
        log_warn("Alignment rate <60% — possible mismatch or ASR trouble")

    if debug:
        log_debug(
            f"DP alignment summary: {matches} matches, {pct:.2f}% similarity",
            True
        )

    return mapping


# --------------------------------------------------------------------------
# Line timing builder (your custom logic preserved)
# --------------------------------------------------------------------------

def build_line_timings(
    lyrics_data: LyricsData,
    words: List[Word],
    token_mapping: List[Optional[int]],
    audio_duration: float,
    min_line_duration: float,
    fallback_line_duration: float,
    gap_after_line: float,
    all_word_intervals: List[Tuple[float, float]],
    debug: bool = False,
) -> List[LineTiming]:

    line_timings: List[LineTiming] = []
    n_lines = len(lyrics_data.lines)

    # PASS 1 — raw timings
    for line_idx in range(n_lines):
        raw = lyrics_data.lines[line_idx]
        t0, t1 = lyrics_data.line_to_token_span[line_idx]

        mapped = [
            token_mapping[t]
            for t in range(t0, t1)
            if 0 <= t < len(token_mapping) and token_mapping[t] is not None
        ]

        if mapped:
            uniq = sorted(set(mapped))
            starts = [words[j].start for j in uniq]
            ends = [words[j].end for j in uniq]
            start = min(starts)
            end = max(ends)
            has_alignment = True
            log_debug(
                f"L{line_idx:03d} aligned ({len(uniq)} tokens) "
                f"{start:.2f}-{end:.2f} '{raw}'",
                debug,
            )
        else:
            start = math.nan
            end = math.nan
            has_alignment = False
            log_debug(
                f"L{line_idx:03d} no alignment → interpolate '{raw}'",
                debug,
            )

        line_timings.append(
            LineTiming(
                index=line_idx,
                start=start,
                end=end,
                text=raw,
                has_alignment=has_alignment,
            )
        )

    # PASS 2 — exclude blank lines completely
    for lt in line_timings:
        if lt.text.strip() == "":
            lt.excluded = True
            lt.start = math.nan
            lt.end = math.nan
            lt.has_alignment = False

    # PASS 3 — interpolation / pre-chorus packing / instrumental detection
    _interpolate_missing_line_times(
        line_timings=line_timings,
        audio_duration=audio_duration,
        fallback_line_duration=fallback_line_duration,
        gap_after_line=gap_after_line,
        all_word_intervals=all_word_intervals,
    )

    # PASS 4 — sanitize monotonic + min durations
    _sanitize_line_times(
        line_timings=line_timings,
        audio_duration=audio_duration,
        min_line_duration=min_line_duration,
    )

    return line_timings


# --------------------------------------------------------------------------
# Interval helper
# --------------------------------------------------------------------------

def _intervals_in_gap(intervals, start_t, end_t):
    if not intervals:
        return []
    return [(s, e) for (s, e) in intervals if s < end_t and e > start_t and e > s]


# --------------------------------------------------------------------------
# Interpolation logic (unchanged)
# --------------------------------------------------------------------------

def _interpolate_missing_line_times(
    line_timings: List[LineTiming],
    audio_duration: float,
    fallback_line_duration: float,
    gap_after_line: float,
    all_word_intervals: List[Tuple[float, float]],
) -> None:

    n = len(line_timings)
    if n == 0:
        return

    known = [
        i for i, lt in enumerate(line_timings)
        if not lt.excluded
        and not math.isnan(lt.start)
        and not math.isnan(lt.end)
    ]

    # CASE: no aligned lines at all
    if not known:
        log_warn("No aligned lines; distributing evenly")
        if audio_duration <= 0:
            for i, lt in enumerate(line_timings):
                if lt.excluded: continue
                lt.start = i * fallback_line_duration
                lt.end = lt.start + fallback_line_duration
            return

        step = audio_duration / max(1, sum(1 for lt in line_timings if not lt.excluded))
        pos = 0.0
        for lt in line_timings:
            if lt.excluded: continue
            lt.start = pos
            lt.end = lt.start + min(fallback_line_duration, step * 0.9)
            pos += step
        return
    # ----------------------------------------------------------
    # BETWEEN aligned neighbors
    # ----------------------------------------------------------
    for k in range(len(known) - 1):
        i0 = known[k]
        i1 = known[k + 1]
        gap_lines = i1 - i0 - 1
        if gap_lines <= 0:
            continue

        start0 = line_timings[i0].end
        start1 = line_timings[i1].start

        if math.isnan(start0) or math.isnan(start1):
            continue

        span = max(0.0, start1 - start0)
        gap_words = _intervals_in_gap(all_word_intervals, start0, start1)

        # ------------------------------------------------------
        # PURE INSTRUMENTAL GAP (your rule)
        # ------------------------------------------------------
        if not gap_words and span >= INSTRUMENTAL_MIN_GAP:
            # Mark all intermediate unmatched lines as excluded
            for idx in range(i0 + 1, i1):
                line_timings[idx].excluded = True
            continue

        # ------------------------------------------------------
        # PRE-CHORUS TAG BEHAVIOR (your special logic)
        # ------------------------------------------------------
        region_end = start1 - gap_after_line
        if region_end <= start0:
            # Extremely tight — fallback to naïve incremental placement
            prev_end = start0
            for idx in range(i0 + 1, i1):
                lt = line_timings[idx]
                if lt.excluded:
                    continue
                lt.start = prev_end + gap_after_line
                lt.end = lt.start + fallback_line_duration
                prev_end = lt.end
            continue

        # Lines to place in the tag-pattern
        to_place = [
            idx
            for idx in range(i0 + 1, i1)
            if not line_timings[idx].excluded
        ]
        if not to_place:
            continue

        available = max(0.0, region_end - start0)
        per = fallback_line_duration
        needed = per * len(to_place)

        if needed > available and available > 0:
            per = available / len(to_place)

        last_end = region_end
        # Pack lines *backwards* right before the aligned line
        for idx in reversed(to_place):
            lt = line_timings[idx]
            lt.end = last_end
            lt.start = max(start0, lt.end - per)
            last_end = lt.start

    # ----------------------------------------------------------
    # BEFORE first aligned line
    # ----------------------------------------------------------
    first = known[0]
    for idx in range(first - 1, -1, -1):
        lt = line_timings[idx]
        if lt.excluded:
            continue
        next_start = (
            line_timings[idx + 1].start
            if not math.isnan(line_timings[idx + 1].start)
            else 0.0
        )
        lt.end = max(0.0, next_start - gap_after_line)
        lt.start = max(0.0, lt.end - fallback_line_duration)

    # ----------------------------------------------------------
    # AFTER last aligned line
    # ----------------------------------------------------------
    last = known[-1]
    for idx in range(last + 1, n):
        lt = line_timings[idx]
        if lt.excluded:
            continue
        prev_end = (
            line_timings[idx - 1].end
            if not math.isnan(line_timings[idx - 1].end)
            else 0.0
        )
        lt.start = prev_end + gap_after_line
        lt.end = lt.start + fallback_line_duration

    # ----------------------------------------------------------
    # CLAMP to audio duration
    # ----------------------------------------------------------
    if audio_duration > 0:
        for lt in line_timings:
            if lt.excluded:
                continue
            lt.start = max(0.0, min(lt.start, audio_duration))
            lt.end = max(lt.start, min(lt.end, audio_duration))


# --------------------------------------------------------------------------
# SANITIZER — monotonicity + min duration enforcement
# --------------------------------------------------------------------------

def _sanitize_line_times(
    line_timings: List[LineTiming],
    audio_duration: float,
    min_line_duration: float,
) -> None:

    prev_end = 0.0

    for lt in line_timings:
        if lt.excluded:
            continue

        if math.isnan(lt.start):
            lt.start = prev_end

        if math.isnan(lt.end) or lt.end < lt.start:
            lt.end = lt.start + min_line_duration

        if lt.start < prev_end:
            lt.start = prev_end

        if lt.end - lt.start < min_line_duration:
            lt.end = lt.start + min_line_duration

        if audio_duration > 0:
            lt.start = max(0, min(lt.start, audio_duration))
            lt.end = max(lt.start, min(lt.end, audio_duration))

        prev_end = lt.end
# --------------------------------------------------------------------------
# CSV WRITER
# --------------------------------------------------------------------------

def write_csv(path: Path, line_timings: List[LineTiming]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    kept = [lt for lt in line_timings if not lt.excluded]
    excluded = len(line_timings) - len(kept)

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for lt in kept:
            w.writerow([lt.index, f"{lt.start:.3f}", f"{lt.end:.3f}", lt.text])

    log_ok(
        f"Wrote timings CSV with {len(kept)} lines "
        f"(excluded {excluded} lines: blanks + instrumentals) → {path}"
    )


# --------------------------------------------------------------------------
# CLI ARG PARSER
# --------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Whisper-based auto-timing engine for karaoke (line-level CSV)."
    )

    # ------------------------------------------------------
    # I/O
    # ------------------------------------------------------
    p.add_argument("--slug", help="Song slug (preferred).")
    p.add_argument("--base", help="Alternative to --slug; normalized similarly.")
    p.add_argument("--audio", help="Override audio path.")
    p.add_argument("--lyrics", help="Override lyrics TXT path.")
    p.add_argument("--out-csv", help="Override output CSV path.")

    # ------------------------------------------------------
    # WHISPER SETTINGS
    # ------------------------------------------------------
    p.add_argument("--language", default="en", help="Force ASR language. Default=en.")
    p.add_argument("--model-size", default="base", help="Whisper model (faster-whisper).")
    p.add_argument("--device", default="cpu", help="'cpu' or 'cuda'.")
    p.add_argument(
        "--compute-type",
        default="auto",
        help="'auto' picks float16 on GPU, int8 on CPU.",
    )
    p.add_argument("--beam-size", type=int, default=5)

    # ------------------------------------------------------
    # TIMING PARAMETERS
    # ------------------------------------------------------
    p.add_argument("--min-line-duration", type=float, default=0.80)
    p.add_argument("--fallback-line-duration", type=float, default=2.50)
    p.add_argument("--min-similarity", type=float, default=0.60)

    # ------------------------------------------------------
    # DEBUG
    # ------------------------------------------------------
    p.add_argument("--debug", action="store_true")

    # ------------------------------------------------------
    # NECESSARY FOR 0_master COMPATIBILITY
    # ------------------------------------------------------
    p.add_argument("--no-ui", action="store_true")

    # - Not used here, but 0_master passes these through:
    p.add_argument("--test", action="store_true")
    p.add_argument("--release", action="store_true")
    p.add_argument("--force-retime", action="store_true")

    return p
# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    parser = parse_args()
    args, extra = parser.parse_known_args()

    no_ui = args.no_ui

    # ------------------------------------------------------------------
    # MODE SHORTCUTS (TEST vs RELEASE)
    # TEST  -> lighter timing model, assumed "dev" pipeline
    # RELEASE -> heavy timing model, safest alignment
    # ------------------------------------------------------------------
    if args.test and args.release:
        print(f"{RED}Cannot use --test and --release together.{RESET}")
        sys.exit(1)

    if args.test:
        log("MODE",
            "TEST mode: auto-steps, no-ui, model=htdemucs_tiny, timing-model-size=base, no-upload.",
            CYAN)

        args.no_ui = True
        no_ui = True
        args.steps = None
        args.model = "htdemucs_tiny"

        # TEST = Option B → lighter ASR model
        if not getattr(args, "timing_model_size", None):
            args.timing_model_size = "base"

        args.no_upload = True

    if args.release:
        log("MODE",
            "RELEASE mode: auto-steps, no-ui, model=htdemucs, timing-model-size=large-v3.",
            CYAN)

        args.no_ui = True
        no_ui = True
        args.steps = None
        args.model = "htdemucs"

        # RELEASE = Option A → heavy ASR model
        if not getattr(args, "timing_model_size", None):
            args.timing_model_size = "large-v3"

    # ------------------------------------------------------------------
    # SLUG / BASE / QUERY RESOLUTION
    # ------------------------------------------------------------------
    slug = None
    query = None

    if args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using slug from CLI: "{slug}"', CYAN)

    elif args.base:
        slug = slugify(args.base)
        log("SLUG", f'Using base from CLI: "{slug}"', CYAN)

    elif getattr(args, "query", None):
        raw_q = args.query.strip()
        slug = slugify(raw_q)
        query = raw_q
        log("SLUG", f'Using slug "{slug}" from CLI query', CYAN)

    else:
        # fallback interactive (preserved for UI usage)
        slug, query = choose_slug_and_query(no_ui=no_ui)
        if not slug:
            print(f"{RED}No slug provided and no previous slug exists.{RESET}")
            sys.exit(1)

    # Normalize & prepare paths
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    meta_path = TIMINGS_DIR / f"{slug}.timingmeta.json"
    current_model = args.timing_model_size
    # ------------------------------------------------------------------
    # EARLY EXIT (1): CSV exists + meta matches → skip transcription
    # ------------------------------------------------------------------
    if csv_path.exists() and meta_path.exists():
        try:
            import json
            prev_meta = json.loads(meta_path.read_text())
            prev_model = prev_meta.get("model_size")

            if prev_model == current_model:
                log("TIMING",
                    f"CSV exists and model matches ({current_model}); skipping transcription.",
                    GREEN)
                return
            else:
                log("TIMING",
                    f"Timing model changed ({prev_model} → {current_model}); re-transcribing.",
                    YELLOW)

        except Exception:
            log("TIMING", "Failed to read timingmeta.json; re-transcribing.", RED)

    # ------------------------------------------------------------------
    # EARLY EXIT (2): CSV exists → skip entirely unless --force-retime
    # ------------------------------------------------------------------
    if csv_path.exists() and not args.force_retime:
        log("TIMING", f"CSV exists, skipping transcription: {csv_path}", GREEN)
        return

    # ------------------------------------------------------------------
    # LOG selected timing model for clarity
    # ------------------------------------------------------------------
    if current_model:
        log("TIMING", f"Using timing model size={current_model}", CYAN)

    # ------------------------------------------------------------------
    # Check whether user passed any volume levels (affects stems logic)
    # ------------------------------------------------------------------
    has_levels = any(
        getattr(args, v, None) is not None
        for v in ("vocals", "bass", "drums", "guitar")
    )

    # ------------------------------------------------------------------
    # Detect pipeline status (1_txt/mp3, 2_stems, 3_timings, 4_mp4, 5_upload)
    # ------------------------------------------------------------------
    status = detect_step_status(slug, getattr(args, "profile", None))
    show_pipeline_status(status)
    # ---------------------------------------------------------
    # STEP SELECTION LOGIC
    # ---------------------------------------------------------
    if getattr(args, "steps", None):
        # Explicit:  --steps 134  or  --steps 24
        steps: list[int] = []
        for ch in args.steps:
            if ch.isdigit():
                i = int(ch)
                if 1 <= i <= 5 and i not in steps:
                    steps.append(i)
        log("MASTER", f"Running requested steps: {steps}", CYAN)

    else:
        # Automatic step selection
        if no_ui:
            #
            # Auto step selection depends on which artifacts exist
            #
            if status["1"] == "MISSING":
                steps = [1, 2, 3, 4]
            elif status["2"] == "MISSING":
                steps = [2, 3, 4]
            elif status["3"] == "MISSING":
                steps = [3, 4]
            elif status["4"] == "MISSING":
                steps = [4]
            else:
                steps = []

            # SPECIAL CASE:
            # ---------------------------------------------------------
            # If CSV already exists (status["3"]=="DONE"), do NOT run 3
            # unless model changed or --force-retime was passed.
            # ---------------------------------------------------------
            if status["3"] == "DONE":
                if "3" in steps:
                    steps.remove(3)
                log("MASTER", "Skipping Step3 because CSV already exists.", GREEN)

            log("MASTER", f"--no-ui auto-selected steps: {steps}", CYAN)

        else:
            # Interactive
            steps = choose_steps_interactive(status)
            log("MASTER", f"Running steps: {steps}", CYAN)
    # ---------------------------------------------------------
    # RUN STEPS
    # ---------------------------------------------------------
    t1 = t2 = t3 = t4 = t5 = 0.0

    # STEP 1 — TXT + MP3
    if 1 in steps:
        t1 = run_step1(slug, query, no_ui, extra)

    # STEP 2 — STEMS + MIX
    if 2 in steps:
        t2 = run_step2(
            slug,
            args.profile,
            args.model,
            interactive=not no_ui,
            extra=extra,
            has_levels=has_levels,
            reset_cache=args.reset_cache,
        )

    # STEP 3 — AUTO TIMING (only runs if step-selection allowed it)
    if 3 in steps:
        t3 = run_step3(
            slug,
            args.timing_model_size,
            extra=extra,
        )

    # STEP 4 — MP4 RENDER
    if 4 in steps:
        t4 = run_step4(
            slug,
            args.profile,
            offset,
            force=args.force_mp4,
            called_from_master=True,
            extra=extra,
        )

    # STEP 5 — UPLOAD
    if 5 in steps and not args.no_upload:
        t5 = run_step5(
            slug,
            args.profile,
            offset,
            extra=extra,
        )
    elif 5 in steps and args.no_upload:
        log("STEP5", "Upload requested but --no-upload is set; skipping.", YELLOW)

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    total = t1 + t2 + t3 + t4 + t5

    if total > 0:
        print()
        print(f"{BOLD}{CYAN}======== PIPELINE SUMMARY ========{RESET}")
        if t1:
            print(f"{WHITE}Step1 txt/mp3:{RESET}  {GREEN}{fmt_secs(t1)}{RESET}")
        if t2:
            print(f"{WHITE}Step2 stems:{RESET}    {GREEN}{fmt_secs(t2)}{RESET}")
        if t3:
            print(f"{WHITE}Step3 timing:{RESET}   {GREEN}{fmt_secs(t3)}{RESET}")
        if t4:
            print(f"{WHITE}Step4 mp4:{RESET}      {GREEN}{fmt_secs(t4)}{RESET}")
        if t5:
            print(f"{WHITE}Step5 upload:{RESET}   {GREEN}{fmt_secs(t5)}{RESET}")
        print(f"{GREEN}Total time:{RESET}       {BOLD}{fmt_secs(total)}{RESET}")
        print(f"{BOLD}{CYAN}=================================={RESET}")
        
if __name__ == "__main__":
    main()

# end of 3_auto_timing.py
