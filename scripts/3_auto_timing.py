#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# AUTO-TIMING (WHISPERX-ONLY) ENGINE
#
# Purpose:
#   - Take txts/<slug>.txt and audio for <slug>
#   - Run WhisperX ASR + forced alignment on the *mix/karaoke* audio (when available)
#   - Align lyric lines to ASR word tokens (monotone, best-match)
#   - Sanitize timings (no early lyrics, no overlaps, no end-past-song)
#   - Emit canonical CSV for 4_mp4.py:
#         line_index,start,end,text
#
# Design notes:
#   - Engine: WhisperX (ASR + wav2vec2-based align)
#   - No external HTTP services, no Gentle
#   - Audio preference: mixes/<slug>_karaoke.wav, then mp3s/<slug>.mp3, then wavs/<slug>.wav
#   - Fully colorized logs for visibility
#
# Usage:
#   python3 scripts/3_auto_timing.py --slug nirvana_come_as_you_are [--debug]
# ---------------------------------------------------------------------------

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple, Any

# ------------------ Colors ------------------
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAG = "\033[35m"
WHITE = "\033[97m"
BLUE = "\033[34m"


def log(tag: str, msg: str, color: str = RESET) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}] {msg}{RESET}")


# ------------------ Paths ------------------
# IMPORTANT: BASE = repo root, not scripts/
BASE = Path(__file__).resolve().parent.parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
WAV_DIR = BASE / "wavs"
MIXES_DIR = BASE / "mixes"
TIMINGS_DIR = BASE / "timings"
META_DIR = BASE / "meta"

TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

# ------------------ Small helpers ------------------


def norm_tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def read_lyrics_lines(path: Path) -> List[str]:
    lines: List[str] = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if s:
            lines.append(s)
    return lines


def pick_audio_for_timing(slug: str) -> Path:
    """
    Prefer the same audio flavor Step 4 uses (karaoke mix) so timing
    matches what the user hears, then fall back to mp3/wav.
    """
    candidates = [
        MIXES_DIR / f"{slug}_karaoke.wav",
        MIXES_DIR / f"{slug}.wav",
        MP3_DIR / f"{slug}.mp3",
        WAV_DIR / f"{slug}.wav",
    ]
    for c in candidates:
        if c.exists():
            log("AUDIO", f"Using audio for timing: {c}", GREEN)
            return c

    log("AUDIO", f"No audio found for slug={slug}", RED)
    log("AUDIO", f"Looked in mixes/mp3s/wavs with slug={slug}", RED)
    sys.exit(1)


def audio_duration_seconds(path: Path) -> float:
    """
    Use librosa to get precise duration.
    """
    try:
        import librosa
    except ImportError:
        log("DUR", "librosa missing — pip install librosa", RED)
        return 0.0

    y, sr = librosa.load(str(path), sr=None, mono=True)
    if sr <= 0:
        return 0.0
    return len(y) / float(sr)


# ------------------ WhisperX engine ------------------


def run_whisperx_on_audio(audio_path: Path, lang: str = "en") -> Dict[str, Any]:
    """
    Run WhisperX ASR + alignment and return:
        {
          "segments": [...],
          "word_segments": [...]
        }
    where word_segments = [{ "word": str, "start": float, "end": float }, ...]
    """
    log("WX", f"Loading WhisperX ASR model (medium)…", CYAN)

    import torch  # type: ignore
    import whisperx  # type: ignore

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    model = whisperx.load_model(f"medium", device=device, compute_type=compute_type)
    log("WX", f"Running ASR on {audio_path}…", MAG)
    result = model.transcribe(str(audio_path), batch_size=16)

    # Load alignment model
    log("WX", "Loading alignment model (wav2vec2)…", CYAN)
    model_a, metadata = whisperx.load_align_model(
        language_code=result.get("language", lang),
        device=device,
    )

    log("WX", "Running forced alignment (word-level)…", MAG)
    aligned = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        str(audio_path),
        device=device,
    )

    word_segments = aligned.get("word_segments") or []
    log("WX", f"WhisperX word segments: {len(word_segments)}", GREEN)

    # Preview a few
    for i, w in enumerate(word_segments[:10]):
        log(
            "WX",
            f"  WORD[{i}] '{w.get('word','')}' {w.get('start',0):.3f}-{w.get('end',0):.3f}",
            WHITE,
        )

    return {"segments": result.get("segments", []), "word_segments": word_segments}


# ------------------ Alignment on top of WhisperX tokens ------------------


def tokens_from_word_segments(word_segments: List[Dict[str, Any]]) -> Tuple[List[str], List[float]]:
    tokens: List[str] = []
    times: List[float] = []

    for w in word_segments:
        text = str(w.get("word", "") or "")
        start = float(w.get("start", 0.0) or 0.0)
        for t in norm_tokens(text):
            tokens.append(t)
            times.append(start)

    return tokens, times


def align_lines_to_tokens(
    lines: List[str],
    tokens: List[str],
    token_times: List[float],
    coverage_thresh: float = 0.50,
    search_pad: int = 80,
    gap_fallback: float = 1.75,
) -> List[Tuple[int, float, float, str]]:
    """
    Monotone, greedy-best alignment of lyric lines to ASR tokens.

    - Only moves forward through the token stream (cursor)
    - For each line, search a sliding window of tokens [cursor, cursor+search_pad]
    - Pick the j..k span with max overlap ratio (hits / len(line_tokens))
    - If no good match, schedule line after previous line (gap_fallback)
    """
    log("ALIGN", "Aligning lyric lines to WhisperX tokens…", CYAN)

    N = len(tokens)
    out: List[Tuple[int, float, float, str]] = []

    if N == 0:
        # Hard fallback: uniform spacing
        log("ALIGN", "No tokens found — falling back to uniform spacing.", YELLOW)
        t = 0.0
        for i, line in enumerate(lines):
            out.append((i, t, t + 0.01, line))
            t += gap_fallback
        return out

    cursor = 0
    earliest_token_time = token_times[0]

    for i, line in enumerate(lines):
        ltoks = norm_tokens(line)
        if not ltoks:
            prev = out[-1][1] if out else earliest_token_time
            out.append((i, prev, prev + 0.01, line))
            continue

        best_score = -1.0
        best_j = None
        best_k = None

        approx_len = max(1, len(ltoks))
        j_start = cursor
        j_end = min(N - 1, cursor + search_pad)

        for j in range(j_start, j_end + 1):
            k_max = min(N, j + approx_len + approx_len // 2 + 1)
            for k in range(j + approx_len, k_max):
                window_toks = tokens[j:k]
                if not window_toks:
                    continue
                # STRICT substring match inside window_toks
                window_str = " ".join(window_toks)
                line_str   = " ".join(ltoks)

                if line_str in window_str:
                    # perfect sequence match
                    score = 1.0
                else:
                    # fallback: partial sequence overlap
                    score = 0.0
                    for j2 in range(len(window_toks) - len(ltoks) + 1):
                        if window_toks[j2:j2+len(ltoks)] == ltoks:
                            score = 0.90
                            break

                # use score
                if score > best_score:
                    best_score = score
                    best_j = j
                    best_k = j + len(ltoks)

        if best_score > 0 and best_j is not None:
            ts = token_times[best_j]
            out.append((i, ts, ts + 0.01, line))
            cursor = max(cursor, best_k or (best_j + 1))
        else:
            # No strong match — schedule after previous line
            if out:
                st = out[-1][1] + gap_fallback
            else:
                st = earliest_token_time
            out.append((i, st, st + 0.01, line))
            cursor = min(N - 1, cursor + approx_len)

    return out


# ------------------ Timing sanitizer ------------------


def sanitize_timings(
    rows: List[Tuple[int, float, float, str]],
    song_duration: float,
    earliest_word_time: float,
    merge_ms: int = 150,
) -> List[Tuple[int, float, float, str]]:
    """
    rows: list of (line_index, start, end, text)
    Enforces:
      - Remove any rows starting at/after song end
      - Snap very early starts up near first speech
      - Merge near-duplicate rows within merge_ms & same text
      - Strictly increasing start times (no backwards jumps)
      - end <= next.start - epsilon
      - last.end <= song_duration - small_pad
    """
    if not rows:
        return rows

    # Convert to dict rows to reuse merge logic
    dict_rows: List[Dict[str, Any]] = []
    for li, st, en, tx in rows:
        dict_rows.append(
            {
                "index": li,
                "start": float(st),
                "end": float(en),
                "text": tx,
            }
        )

    merge_s = merge_ms / 1000.0

    # 1) Sort by start
    dict_rows.sort(key=lambda r: r["start"])

    # 2) Snap lyrics that start *before* first speech up to near it
    min_start_allowed = max(0.0, earliest_word_time - 0.20)
    for r in dict_rows:
        if r["start"] < min_start_allowed:
            r["start"] = min_start_allowed

    # 3) Merge near-duplicates by start time & same text
    merged: List[Dict[str, Any]] = []
    cur: Dict[str, Any] | None = None

    for r in dict_rows:
        if r["start"] >= song_duration:
            # Drop anything at / beyond song duration
            continue

        if cur is None:
            cur = r.copy()
            continue

        if abs(r["start"] - cur["start"]) <= merge_s and r["text"] == cur["text"]:
            cur["end"] = max(cur["end"], r["end"])
        else:
            merged.append(cur)
            cur = r.copy()

    if cur is not None:
        merged.append(cur)

    dict_rows = merged

    # 4) Clamp to song duration and strictly increase start times
    if not dict_rows:
        return []

    clean: List[Dict[str, Any]] = []
    for r in dict_rows:
        if r["start"] >= song_duration:
            continue
        r2 = r.copy()
        # Reserve a little tail padding so we never go past the end
        r2["end"] = min(r2["end"], song_duration - 0.05)
        clean.append(r2)

    dict_rows = clean

    # Strictly increasing starts
    for i in range(1, len(dict_rows)):
        if dict_rows[i]["start"] <= dict_rows[i - 1]["start"]:
            dict_rows[i]["start"] = dict_rows[i - 1]["start"] + 0.001

    # ----------------------------------------------------------------------
    # 5) Convert to tuple rows (preserve index), then sort by line_index
    #    BEFORE expanding durations. This keeps the timing monotone AND
    #    guarantees correct "line stays visible until next line" behavior.
    # ----------------------------------------------------------------------
    final_rows: List[Tuple[int, float, float, str]] = []
    for r in dict_rows:
        final_rows.append(
            (
                int(r["index"]),
                float(r["start"]),
                float(r["end"]),
                str(r["text"]),
            )
        )

    # Sort by line_index for readability and stable ordering
    final_rows.sort(key=lambda x: x[0])

    # ----------------------------------------------------------------------
    # 6) EXPAND DURATIONS PROPERLY
    # Each line should remain visible until *just before* the next line starts.
    # This replaces the old min(end, next.start - eps) behavior, which caused
    # 0.01s flashes for most lyrics.
    # ----------------------------------------------------------------------
    EPS = 0.10  # small gap to prevent overlap in ASS subtitles

    for i in range(len(final_rows) - 1):
        li, st, en, tx = final_rows[i]
        next_st = final_rows[i + 1][1]

        # new end is right before next start
        new_end = max(st + 0.01, next_st - EPS)
        final_rows[i] = (li, st, new_end, tx)

    # Last line ends near song end
    li, st, en, tx = final_rows[-1]
    last_end = max(st + 0.01, song_duration - 0.10)
    final_rows[-1] = (li, st, last_end, tx)

    return final_rows


# ------------------ CSV / debug writers ------------------


def write_canonical_csv(path: Path, rows: List[Tuple[int, float, float, str]]) -> None:
    """
    Canonical CSV header for the entire pipeline:
        line_index,start,end,text
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, st, en, tx in rows:
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", tx])


def write_debug_json(
    dbg_path: Path,
    slug: str,
    word_segments: List[Dict[str, Any]],
    aligned_rows: List[Tuple[int, float, float, str]],
) -> None:
    payload = {
        "slug": slug,
        "version": "3_auto_timing_whisperx_v1",
        "word_segments": word_segments,
        "aligned": [
            {
                "line_index": li,
                "start": st,
                "end": en,
                "text": tx,
            }
            for (li, st, en, tx) in aligned_rows
        ],
    }
    dbg_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ------------------ MAIN PIPELINE ------------------


def run_auto_timing(slug: str, debug: bool = False) -> None:
    log("MAIN", f"=== AUTO-TIMING (WhisperX) START for slug={slug} ===", BOLD + CYAN)

    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log("MAIN", f"Missing txt file: {txt_path}", RED)
        sys.exit(1)

    audio_path = pick_audio_for_timing(slug)

    # Load lyrics
    lines = read_lyrics_lines(txt_path)
    log("LYR", f"Loaded {len(lines)} lyric lines", GREEN)

    # Run WhisperX
    wx = run_whisperx_on_audio(audio_path)
    word_segments = wx["word_segments"]

    if not word_segments:
        log("WX", "No word segments from WhisperX — aborting.", RED)
        sys.exit(1)

    tokens, token_times = tokens_from_word_segments(word_segments)
    earliest_word_time = token_times[0] if token_times else 0.0

    # Align
    aligned = align_lines_to_tokens(lines, tokens, token_times)

    # Compute audio duration for clamping
    song_duration = audio_duration_seconds(audio_path)
    log("DUR", f"Song duration ≈ {song_duration:.3f}s", BLUE)

    # Sanitize
    log(
        "SANITY",
        f"Applying timing sanitizer to {len(aligned)} rows (earliest_word_time={earliest_word_time:.3f})…",
        WHITE + BOLD,
    )
    aligned = sanitize_timings(aligned, song_duration, earliest_word_time)
    log("SANITY", f"→ {len(aligned)} rows after cleaning", GREEN + BOLD)

    # Write CSV
    out_csv = TIMINGS_DIR / f"{slug}.csv"
    write_canonical_csv(out_csv, aligned)
    log("OUT", f"WROTE canonical timings CSV: {out_csv}", GREEN + BOLD)

    # Optional debug JSON
    if debug:
        dbg_path = META_DIR / f"{slug}_whisperx_debug.json"
        write_debug_json(dbg_path, slug, word_segments, aligned)
        log("OUT", f"WROTE debug JSON: {dbg_path}", CYAN)

    log("MAIN", f"=== AUTO-TIMING DONE for slug={slug} ===", GREEN + BOLD)


# ------------------ CLI ------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Auto-timing (WhisperX-only) for karaoke pipeline")
    p.add_argument("--slug", required=True, help="Song slug, e.g. nirvana_come_as_you_are")
    p.add_argument("--debug", action="store_true", help="Write meta/<slug>_whisperx_debug.json")
    args = p.parse_args()

    run_auto_timing(args.slug, debug=args.debug)


if __name__ == "__main__":
    main()

# end of 3_auto_timing.py
