#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# AUTO-TIMING ENGINE (faster-whisper based, line-level CSV)
#
# Purpose:
#   - Take canonical lyrics (txts/<slug>.txt) and audio for <slug>
#   - Run Whisper ASR with word timestamps (no VAD time-compression)
#   - Align lyric tokens to ASR word tokens (monotone, fuzzy)
#   - Derive per-line start/end times from matched tokens
#   - Sanitize timings (monotone, clamped, minimum duration, fallbacks)
#   - Emit canonical CSV for step 4:
#         line_index,start,end,text
#
# Design notes:
#   - Engine: faster-whisper (word_timestamps=True); easy to swap with WhisperX
#   - Audio preference (vocal-heavy first):
#         1) mixes/<slug>_vocals.wav
#         2) mixes/<slug>.wav
#         3) mp3s/<slug>.mp3
#         4) wavs/<slug>.wav
#         5) mixes/<slug>_karaoke.wav  (last resort, weakest vocals)
#   - Lyrics: txts/<slug>.txt (one line per lyric line, no timestamps)
#   - Language:
#         - Default: auto-detect
#         - CLI flag: --language es (or en, etc.)
#         - Normalization is accent-insensitive (good for Spanish)
#   - Robustness:
#         - Instrumental intros/outros handled naturally (no words -> gaps)
#         - Token-level fuzzy alignment using DP
#         - Fallback interpolation for missing lines
#         - “Pre-chorus tag” behavior:
#             * unmatched lines between two aligned lines are packed
#               right before the next aligned line, not smeared
#               across the whole gap.
#         - Gaps with **no ASR words at all** and long enough are
#           treated as instrumental; unmatched lines in those gaps
#           are excluded from CSV.
#         - Blank-lyrics lines (empty or whitespace) are always
#           excluded and never written to CSV, so they never
#           overwrite real lyrics on screen.
#
# Usage examples:
#   python3 scripts/3_auto_timing.py --slug nirvana_come_as_you_are
#   python3 scripts/3_auto_timing.py --slug nirvana_come_as_you_are --language en
#   python3 scripts/3_auto_timing.py --audio mixes/track_vocals.wav --lyrics txts/track.txt
#
from __future__ import annotations

import argparse
import csv
import difflib
import math
import os
import re
import sys
import re
import unicodedata
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# ----- optional rich logging -----
try:
    from rich.console import Console
    from rich.table import Table
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
# Minimal logger (mirrors 0_master.py style)
# --------------------------------------------------------------------------
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _plain_print(tag: str, color: str, msg: str) -> None:
    sys.stderr.write(f"{BOLD}{color}[{tag}]{RESET} {msg}\n")


def log_info(msg: str) -> None:
    if _RICH_AVAILABLE and console is not None:
        console.print(f"[bold cyan][WX][/bold cyan] {msg}")
    else:
        _plain_print("WX", CYAN, msg)


def log_ok(msg: str) -> None:
    if _RICH_AVAILABLE and console is not None:
        console.print(f"[bold green][WX][/bold green] {msg}")
    else:
        _plain_print("WX", GREEN, msg)


def log_warn(msg: str) -> None:
    if _RICH_AVAILABLE and console is not None:
        console.print(f"[bold yellow][WX][/bold yellow] {msg}")
    else:
        _plain_print("WX", YELLOW, msg)


def log_error(msg: str) -> None:
    if _RICH_AVAILABLE and console is not None:
        console.print(f"[bold red][WX][/bold red] {msg}")
    else:
        _plain_print("WX", RED, msg)


def log_debug(msg: str, enabled: bool) -> None:
    if not enabled:
        return
    if _RICH_AVAILABLE and console is not None:
        console.print(f"[bold magenta][WX-DEBUG][/bold magenta] {msg}")
    else:
        _plain_print("WX-DEBUG", MAGENTA, msg)


# ----- paths / constants -----
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
WAVS_DIR = BASE_DIR / "wavs"

DEFAULT_MODEL_SIZE = os.environ.get("WX_MODEL_SIZE", "distil-large-v3")
DEFAULT_MIN_LINE_DURATION = 0.8
DEFAULT_FALLBACK_LINE_DURATION = 2.5
DEFAULT_GAP_AFTER_LINE = 0.1
DEFAULT_MIN_SIMILARITY = 0.6
INSTRUMENTAL_MIN_GAP = 3.0  # s; gap w/ no ASR words >= this gets treated as instrumental


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
    excluded: bool = False  # for instrumental-only gaps or blank lines


# ----- helpers -----
def die(msg: str, code: int = 1) -> None:
    log_error(msg)
    sys.exit(code)


def strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_token(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'")
    text = re.sub(
        r"[^0-9a-záéíóúüñçàèìòùâêîôûäëïöü\s']",
        " ",
        text,
        flags=re.IGNORECASE,
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
        import torch  # type: ignore

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


# ----- audio selection -----
def pick_audio_for_slug(slug: str, explicit_audio: Optional[Path]) -> Path:
    if explicit_audio is not None:
        if not explicit_audio.exists():
            die(f"Explicit audio path does not exist: {explicit_audio}")
        log_info(f"Using explicit audio file: {explicit_audio}")
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

    die(
        f"No audio file found for slug '{slug}'. "
        "Looked in mixes/, mp3s/, wavs/ with expected names."
    )
    assert False


# ----- lyrics -----
def load_lyrics(path: Path, debug: bool = False) -> LyricsData:
    if not path.exists():
        die(f"Lyrics file not found: {path}")

    lines_raw: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            lines_raw.append(raw.rstrip("\n"))

    if not lines_raw:
        die(f"Lyrics file is empty: {path}")

    tokens: List[str] = []
    token_to_line: List[int] = []
    line_to_token_span: List[Tuple[int, int]] = []

    curr = 0
    for idx, line in enumerate(lines_raw):
        line_tokens = tokenize_line(line)
        start = curr
        for t in line_tokens:
            tokens.append(t)
            token_to_line.append(idx)
            curr += 1
        end = curr
        line_to_token_span.append((start, end))
        log_debug(f"L{idx:03d} | raw='{line}' | tokens={line_tokens}", debug)

    if not tokens:
        log_warn(
            "No tokens found in lyrics after normalization. "
            "Lyrics may be only markers/punctuation."
        )

    return LyricsData(
        lines=lines_raw,
        tokens=tokens,
        token_to_line=token_to_line,
        line_to_token_span=line_to_token_span,
    )


# ----- ASR (faster-whisper) -----
def run_asr_with_faster_whisper(
    audio_path: Path,
    model_size: str,
    language: Optional[str],
    device: str,
    compute_type: Optional[str],
    beam_size: int,
    debug: bool = False,
) -> Tuple[List[Word], float]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        die(
            "faster-whisper is not installed. "
            "Install with 'pip3 install faster-whisper' inside your env."
        )

    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    log_info(
        f"Loading Whisper model '{model_size}' on device='{device}', "
        f"compute_type='{compute_type}'..."
    )
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    whisper_lang = None if language in (None, "", "auto") else language
    log_info(
        f"Transcribing {audio_path} "
        f"(language={'auto' if whisper_lang is None else whisper_lang})..."
    )

    segments, info = model.transcribe(
        str(audio_path),
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=False,
        language=whisper_lang,
    )

    words: List[Word] = []
    seg_count = 0
    word_count = 0

    for seg in segments:
        seg_count += 1
        if seg.words is None:
            continue
        for w in seg.words:
            if w.start is None or w.end is None:
                continue
            word_text = w.word.strip()
            if not word_text:
                continue
            words.append(Word(text=word_text, start=float(w.start), end=float(w.end)))
            word_count += 1

    audio_duration = float(getattr(info, "duration", 0.0) or 0.0)
    log_ok(
        f"ASR complete: {seg_count} segments, {word_count} words, "
        f"audio_duration={audio_duration:.3f}s"
    )

    if debug:
        preview = ", ".join(
            f"{w.text}({w.start:.2f}-{w.end:.2f})" for w in words[:25]
        )
        log_debug(f"ASR word preview: {preview}", debug)

    if not words:
        log_warn(
            "ASR produced no word-level output. "
            "Track may be purely instrumental or extremely low volume."
        )

    meta_path = TIMINGS_DIR / f"{slug}.timingmeta.json"
    meta_path.write_text(json.dumps({
        "model_size": args.model_size,
        "timestamp": datetime.now().isoformat()
    }, indent=2))

    return words, audio_duration


# ----- alignment -----
def token_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def align_tokens_dp(
    lyrics_tokens: List[str],
    asr_tokens: List[str],
    min_similarity: float,
    debug: bool = False,
) -> List[Optional[int]]:
    n = len(lyrics_tokens)
    m = len(asr_tokens)

    if n == 0:
        log_warn("No lyrics tokens to align. Skipping alignment.")
        return []

    gap_cost = 1.0
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 0=sub, 1=del, 2=ins

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

    mapping: List[Optional[int]] = [None] * n
    i, j = n, m
    matches = 0

    while i > 0 or j > 0:
        op = back[i][j]
        if op == 0:
            lt = lyrics_tokens[i - 1]
            at = asr_tokens[j - 1]
            sim = token_similarity(lt, at)
            if sim >= min_similarity:
                mapping[i - 1] = j - 1
                matches += 1
            i -= 1
            j -= 1
        elif op == 1:
            i -= 1
        else:
            j -= 1

    match_pct = (matches / n * 100.0) if n > 0 else 0.0
    log_ok(
        f"Token alignment: {matches}/{n} lyrics tokens matched "
        f"({match_pct:.1f}%)."
    )
    if match_pct < 60.0:
        log_warn(
            "Low token alignment rate (<60%). "
            "Audio/lyrics may not match or ASR struggled."
        )

    if debug:
        log_debug(
            f"Alignment summary: lyrics_tokens={n}, asr_tokens={m}, "
            f"matches={matches}, match_pct={match_pct:.1f}%",
            debug,
        )

    return mapping


# ----- line timings -----
def build_line_timings(
    lyrics_data: LyricsData,
    words: List[Word],
    token_mapping: List[Optional[int]],  # lyric token -> word index
    audio_duration: float,
    min_line_duration: float,
    fallback_line_duration: float,
    gap_after_line: float,
    all_word_intervals: List[Tuple[float, float]],
    debug: bool = False,
) -> List[LineTiming]:
    line_timings: List[LineTiming] = []
    n_lines = len(lyrics_data.lines)

    # First pass: build raw timings (with NaNs for unaligned), but DO NOT
    # exclude blanks yet so they can help debug if needed.
    for line_idx in range(n_lines):
        raw_text = lyrics_data.lines[line_idx]
        t_start, t_end = lyrics_data.line_to_token_span[line_idx]
        mapped_indices = [
            token_mapping[ti]
            for ti in range(t_start, t_end)
            if 0 <= ti < len(token_mapping) and token_mapping[ti] is not None
        ]

        if mapped_indices:
            uniq = sorted(set(mapped_indices))
            starts = [words[j].start for j in uniq]
            ends = [words[j].end for j in uniq]
            start = min(starts)
            end = max(ends)
            has_alignment = True
            log_debug(
                f"Line {line_idx:03d} aligned via {len(uniq)} tokens: "
                f"{start:.3f}-{end:.3f} '{raw_text}'",
                debug,
            )
        else:
            start = math.nan
            end = math.nan
            has_alignment = False
            log_debug(
                f"Line {line_idx:03d} has no aligned tokens (will interpolate): "
                f"'{raw_text}'",
                debug,
            )

        line_timings.append(
            LineTiming(
                index=line_idx,
                start=start,
                end=end,
                text=raw_text,
                has_alignment=has_alignment,
            )
        )

    # SECOND PASS: blank-lines exclusion policy
    # Any blank/whitespace-only lyric line is permanently excluded from timing
    # and CSV output. This guarantees blank lines never overwrite real lyrics.
    for lt in line_timings:
        if lt.text.strip() == "":
            lt.excluded = True
            lt.has_alignment = False
            lt.start = math.nan
            lt.end = math.nan

    _interpolate_missing_line_times(
        line_timings=line_timings,
        audio_duration=audio_duration,
        fallback_line_duration=fallback_line_duration,
        gap_after_line=gap_after_line,
        all_word_intervals=all_word_intervals,
    )

    _sanitize_line_times(
        line_timings=line_timings,
        audio_duration=audio_duration,
        min_line_duration=min_line_duration,
    )

    return line_timings


def _intervals_in_gap(
    intervals: List[Tuple[float, float]],
    start_t: float,
    end_t: float,
) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    return [(s, e) for (s, e) in intervals if s < end_t and e > start_t and e > s]


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
        i
        for i, lt in enumerate(line_timings)
        if (not lt.excluded)
        and (not math.isnan(lt.start))
        and (not math.isnan(lt.end))
    ]

    if not known:
        log_warn(
            "No lines have ASR-based timings. "
            "Spreading lines evenly across audio duration."
        )
        if audio_duration <= 0.0:
            for i, lt in enumerate(line_timings):
                if lt.excluded:
                    continue
                lt.start = i * fallback_line_duration
                lt.end = lt.start + fallback_line_duration
            return
        step = audio_duration / max(1, sum(1 for lt in line_timings if not lt.excluded))
        pos = 0.0
        for lt in line_timings:
            if lt.excluded:
                continue
            lt.start = pos
            lt.end = lt.start + min(fallback_line_duration, step * 0.9)
            pos += step
        return

    # between known neighbors
    for idx in range(len(known) - 1):
        i0 = known[idx]
        i1 = known[idx + 1]
        gap_lines = i1 - i0 - 1
        if gap_lines <= 0:
            continue

        start0 = line_timings[i0].end
        start1 = line_timings[i1].start
        if math.isnan(start0) or math.isnan(start1):
            continue
        span = max(0.0, start1 - start0)

        gap_all_words = _intervals_in_gap(all_word_intervals, start0, start1)

        # pure instrumental gap: no ASR words, long enough
        if not gap_all_words and span >= INSTRUMENTAL_MIN_GAP:
            for k in range(1, gap_lines + 1):
                line_timings[i0 + k].excluded = True
            continue

        # PRE-CHORUS TAG behavior:
        # pack unmatched (non-excluded) lines right before the next aligned line (i1).
        region_end = start1 - gap_after_line
        if region_end <= start0:
            # extremely tight; just fall back to naive incremental placement
            prev_end = start0
            for k in range(1, gap_lines + 1):
                i = i0 + k
                lt = line_timings[i]
                if lt.excluded:
                    continue
                lt.start = prev_end + gap_after_line
                lt.end = lt.start + fallback_line_duration
                prev_end = lt.end
            continue

        lines_to_place = [
            i
            for i in range(i0 + 1, i1)
            if not line_timings[i].excluded
        ]
        if not lines_to_place:
            continue

        available = max(0.0, region_end - start0)
        per = fallback_line_duration
        total_needed = per * len(lines_to_place)
        if total_needed > available and available > 0:
            per = available / len(lines_to_place)

        last_end = region_end
        for i in reversed(lines_to_place):
            lt = line_timings[i]
            lt.end = last_end
            lt.start = max(start0, lt.end - per)
            last_end = lt.start

    # before first known
    first_known = known[0]
    for i in range(first_known - 1, -1, -1):
        lt = line_timings[i]
        if lt.excluded:
            continue
        next_start = (
            line_timings[i + 1].start
            if not math.isnan(line_timings[i + 1].start)
            else 0.0
        )
        lt.end = max(0.0, next_start - gap_after_line)
        lt.start = max(0.0, lt.end - fallback_line_duration)

    # after last known
    last_known = known[-1]
    for i in range(last_known + 1, n):
        lt = line_timings[i]
        if lt.excluded:
            continue
        prev_end = (
            line_timings[i - 1].end
            if not math.isnan(line_timings[i - 1].end)
            else 0.0
        )
        lt.start = prev_end + gap_after_line
        lt.end = lt.start + fallback_line_duration

    if audio_duration > 0.0:
        for lt in line_timings:
            if lt.excluded:
                continue
            lt.start = max(0.0, min(lt.start, audio_duration))
            lt.end = max(lt.start, min(lt.end, audio_duration))


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
        if audio_duration > 0.0:
            lt.start = max(0.0, min(lt.start, audio_duration))
            lt.end = max(lt.start, min(lt.end, audio_duration))
        prev_end = lt.end


# ----- CSV writer -----
def write_csv(path: Path, line_timings: List[LineTiming]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [lt for lt in line_timings if not lt.excluded]
    excluded = len(line_timings) - len(kept)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "start", "end", "text"])
        for lt in kept:
            writer.writerow([lt.index, f"{lt.start:.3f}", f"{lt.end:.3f}", lt.text])

    log_ok(
        f"Wrote timings CSV with {len(kept)} lines "
        f"(excluded {excluded} lines: instrumentals + blanks) to {path}"
    )


# ----- CLI -----
def parse_args():
    p = argparse.ArgumentParser(
        description="Whisper-based auto-timing engine for karaoke."
    )

    p.add_argument("--slug")
    p.add_argument("--base", help="Optional base name override")
    p.add_argument("--audio")
    p.add_argument("--lyrics")
    p.add_argument("--out-csv")

    p.add_argument("--language", default="en")
    p.add_argument("--model-size", default="base")

    p.add_argument("--device", default="cpu")
    p.add_argument("--compute-type", default="auto")
    p.add_argument("--beam-size", type=int, default=5)

    p.add_argument("--min-line-duration", type=float, default=0.80)
    p.add_argument("--fallback-line-duration", type=float, default=2.50)
    p.add_argument("--min-similarity", type=float, default=0.60)

    p.add_argument("--debug", action="store_true")

    # required for 0_master.py
    p.add_argument("--no-ui", action="store_true")

    # required because 0_master passes these flags indirectly
    p.add_argument("--test", action="store_true")
    p.add_argument("--release", action="store_true")
    p.add_argument("--force-retime", action="store_true")

    return p

# ----- main -----
def main():
    parser = parse_args()
    args, extra = parser.parse_known_args()
    no_ui = args.no_ui

    # ---------------------------------------------------------
    # MODE SHORTCUTS: --test and --release
    # ---------------------------------------------------------
    if args.test and args.release:
        print(f"{RED}Cannot use --test and --release together.{RESET}")
        sys.exit(1)

    if args.test:
        log(
            "MODE",
            "TEST mode: auto-steps, no-ui, model=htdemucs_tiny, timing-model-size=base, no-upload.",
            CYAN,
        )
        args.no_ui = True
        no_ui = True
        args.steps = None                      # <<< FIXED
        args.model = "htdemucs_tiny"
        if not args.timing_model_size:
            args.timing_model_size = "base"
        args.no_upload = True

    if args.release:
        log(
            "MODE",
            "RELEASE mode: auto-steps, no-ui, model=htdemucs, timing-model-size=large-v3.",
            CYAN,
        )
        args.no_ui = True
        no_ui = True
        args.steps = None                      # <<< FIXED
        args.model = "htdemucs"
        if not args.timing_model_size:
            args.timing_model_size = "large-v3"

    slug: str | None = None
    query: str | None = None

    # ----------------------------------------------------------------------
    # SLUG & QUERY
    # ----------------------------------------------------------------------
    slug: str | None = None
    query: str | None = None

    if args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using slug from CLI: "{slug}"', CYAN)

    elif args.base:
        slug = slugify(args.base)
        log("SLUG", f'Using base from CLI: "{slug}"', CYAN)

    elif args.query:
        raw_q = args.query.strip()
        slug = slugify(raw_q)
        query = raw_q
        log("SLUG", f'Using slug "{slug}" from CLI query', CYAN)

    else:
        slug, query = choose_slug_and_query(no_ui=no_ui)
        if not slug:
            print(f"{RED}No slug provided and no previous slug exists.{RESET}")
            sys.exit(1)

    # ----------------------------------------------------------------------
    # EARLY EXIT: CSV exists AND model matches → skip transcription
    # ----------------------------------------------------------------------
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    meta_path = TIMINGS_DIR / f"{slug}.timingmeta.json"
    current_model = args.model_size

    if csv_path.exists() and meta_path.exists():
        try:
            import json
            prev = json.loads(meta_path.read_text())

            prev_model = prev.get("model_size")

            if prev_model == current_model:
                log("TIMING", f"CSV exists and model matches ({current_model}); skipping transcription.", GREEN)
                return
            else:
                log("TIMING", f"Model changed ({prev_model} → {current_model}); re-transcribing.", YELLOW)

        except Exception:
            log("TIMING", "Meta read failed; re-transcribing.", RED)


    # ----------------------------------------------------------------------
    # EARLY EXIT: CSV already exists → skip transcription entirely
    # ----------------------------------------------------------------------
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    if csv_path.exists() and not args.force_retime:
        log("TIMING", f"CSV exists, skipping transcription: {csv_path}", GREEN)
        return

    if args.timing_model_size:
        log("TIMING", f"Using timing model size={args.timing_model_size}", CYAN)

    # Determine if any CLI levels were provided
    has_levels = any(
        v is not None
        for v in (args.vocals, args.bass, args.drums, args.guitar)
    )

    status = detect_step_status(slug, args.profile)
    show_pipeline_status(status)

    # ---------------------------------------------------------
    # STEP SELECTION (respect auto-selection)
    # ---------------------------------------------------------
    if args.steps:
        steps: list[int] = []
        for ch in args.steps:
            if ch.isdigit():
                i = int(ch)
                if 1 <= i <= 5 and i not in steps:
                    steps.append(i)
        log("MASTER", f"Running requested steps: {steps}", CYAN)
    else:
        if no_ui:
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

            # <<< SPECIAL CASE: if CSV exists, DO NOT run step 3
            if "3" in status and status["3"] == "DONE" and 3 in steps:
                steps.remove(3)

            log("MASTER", f"--no-ui auto-selected steps: {steps}", CYAN)
        else:
            steps = choose_steps_interactive(status)
            log("MASTER", f"Running steps: {steps}", CYAN)

    # ---------------------------------------------------------
    # RUN STEPS
    # ---------------------------------------------------------
    t1 = t2 = t3 = t4 = t5 = 0.0

    if 1 in steps:
        t1 = run_step1(slug, query, no_ui, extra)

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

    if 3 in steps:
        t3 = run_step3(slug, args.timing_model_size, extra=extra)

    if 4 in steps:
        t4 = run_step4(
            slug,
            args.profile,
            offset,
            force=args.force_mp4,
            called_from_master=True,
            extra=extra,
        )

    if 5 in steps and not args.no_upload:
        t5 = run_step5(slug, args.profile, offset, extra=extra)
    elif 5 in steps and args.no_upload:
        log("STEP5", "Upload requested but --no-upload is set; skipping.", YELLOW)

    total = t1 + t2 + t3 + t4 + t5

    if total > 0:
        print()
        print(f"{BOLD}{CYAN}======== PIPELINE SUMMARY ========{RESET}")
        if t1: print(f"{WHITE}Step1 txt/mp3:{RESET}  {GREEN}{fmt_secs(t1)}{RESET}")
        if t2: print(f"{WHITE}Step2 stems:{RESET}    {GREEN}{fmt_secs(t2)}{RESET}")
        if t3: print(f"{WHITE}Step3 timing:{RESET}   {GREEN}{fmt_secs(t3)}{RESET}")
        if t4: print(f"{WHITE}Step4 mp4:{RESET}      {GREEN}{fmt_secs(t4)}{RESET}")
        if t5: print(f"{WHITE}Step5 upload:{RESET}   {GREEN}{fmt_secs(t5)}{RESET}")
        print(f"{GREEN}Total time:{RESET}       {BOLD}{fmt_secs(total)}{RESET}")
        print(f"{BOLD}{CYAN}=================================={RESET}")

if __name__ == "__main__":
    main()

# end of 3_auto_timing.py
