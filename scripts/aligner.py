# scripts/aligner.py
# Lightweight lyrics↔audio alignment helpers used by both CLI and service.
# Writes a CSV: header "line,start" with start times in seconds.

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple
import csv
import subprocess
import shlex

# ----- tiny console styling (kept consistent with your style) -----
RESET = "\033[0m"; BOLD = "\033[1m"
CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; BLUE = "\033[34m"
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

# ----- config -----
@dataclass
class AlignConfig:
    pad_head: float = 0.75      # seconds before first line
    pad_tail: float = 0.75      # seconds after last line
    min_step: float = 1.00      # minimum spacing between consecutive lines
    max_step: float = 6.00      # maximum spacing between consecutive lines
    clamp_to_audio: bool = True # keep last start ≤ (duration - pad_tail)

# ----- I/O helpers -----
def read_lyrics_lines(txt_path: Path) -> List[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out: List[str] = []
    for line in raw:
        s = line.strip()
        if not s:
            continue
        # Light filtering of common non-lyric lines (kept deliberately simple)
        if s.startswith("[") and s.endswith("]"):
            continue
        out.append(s)
    return out

def ffprobe_duration_seconds(audio_path: Path) -> float:
    """
    Uses ffprobe to fetch container duration (seconds, float).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
        val = cp.stdout.strip()
        return max(0.0, float(val)) if val else 0.0
    except Exception as e:
        log("ALIGN", f"ffprobe failed: {e}", YELLOW)
        return 0.0

def write_timings_csv(pairs: Iterable[Tuple[str, float]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line", "start"])
        for line, start in pairs:
            # Keep exact seconds; downstream may format to mm:ss
            w.writerow([line, f"{float(start):.6f}"])

# ----- alignment heuristics -----
def naive_align(duration_s: float, lines: List[str], cfg: AlignConfig) -> List[Tuple[str, float]]:
    """
    Uniform spacing across available window [pad_head, duration - pad_tail],
    step clamped to [min_step, max_step].
    """
    if not lines:
        return []
    usable = max(0.0, duration_s - (cfg.pad_head + cfg.pad_tail))
    # Fall back to minimum steps if unusable duration reported
    base_step = usable / max(1, len(lines))
    step = max(cfg.min_step, min(cfg.max_step, base_step if usable > 0 else cfg.min_step))
    starts: List[float] = []
    t = cfg.pad_head
    for _ in lines:
        starts.append(max(0.0, t))
        t += step

    # Clamp tail if requested
    if cfg.clamp_to_audio and duration_s > 0 and starts:
        end_limit = max(0.0, duration_s - cfg.pad_tail)
        if starts[-1] > end_limit:
            shift = starts[-1] - end_limit
            starts = [max(0.0, s - shift) for s in starts]

    return list(zip(lines, starts))

def _length_weight(line: str) -> float:
    # length^0.6 mildly favors longer lines; clamp to [1, 12]
    import math
    return max(1.0, min(12.0, math.pow(max(1, len(line)), 0.6)))

def smart_align(duration_s: float, lines: List[str], cfg: AlignConfig, verbose: bool=False) -> List[Tuple[str, float]]:
    """
    Length-weighted distribution across [pad_head, duration - pad_tail].
    Respects min/max step; falls back to naive for degenerate inputs.
    """
    if not lines:
        return []
    usable = max(0.0, duration_s - (cfg.pad_head + cfg.pad_tail))
    weights = [_length_weight(ln) for ln in lines]
    total_w = sum(weights) or float(len(lines))
    if usable <= 0.1 or total_w <= 0:
        return naive_align(duration_s, lines, cfg)

    starts: List[float] = []
    t = cfg.pad_head
    for w in weights:
        starts.append(t)
        slice_len = usable * (w / total_w)
        # Bound per-line growth
        slice_len = max(cfg.min_step, min(cfg.max_step, slice_len))
        t += slice_len

    if cfg.clamp_to_audio and duration_s > 0 and starts:
        end_limit = max(0.0, duration_s - cfg.pad_tail)
        if starts[-1] > end_limit:
            shift = starts[-1] - end_limit
            starts = [max(0.0, s - shift) for s in starts]

    if verbose:
        log("ALIGN+", f"duration={duration_s:.3f}s usable≈{usable:.3f}s lines={len(lines)}", CYAN)
        log("ALIGN+", f"min_step={cfg.min_step} max_step={cfg.max_step} head={cfg.pad_head} tail={cfg.pad_tail}", CYAN)

    return list(zip(lines, starts))

# ----- top-level helpers -----
def align_txt_to_audio(txt_path: Path, audio_path: Path, out_csv: Path,
                       cfg: AlignConfig | None = None, verbose: bool=False) -> Path:
    cfg = cfg or AlignConfig()
    lines = read_lyrics_lines(txt_path)
    if not lines:
        raise ValueError("no lyric lines found after filtering")
    dur = ffprobe_duration_seconds(audio_path)
    pairs = naive_align(dur, lines, cfg)
    write_timings_csv(pairs, out_csv)
    log("ALIGN", f"[naive] Wrote timings → {out_csv}", GREEN)
    if verbose:
        log("ALIGN", f"Used ffprobe duration={dur:.3f}s, lines={len(lines)}", CYAN)
    return out_csv

def align_txt_to_audio_smart(txt_path: Path, audio_path: Path, out_csv: Path,
                             cfg: AlignConfig | None = None, verbose: bool=False) -> Path:
    cfg = cfg or AlignConfig()
    lines = read_lyrics_lines(txt_path)
    if not lines:
        raise ValueError("no lyric lines found after filtering")
    dur = ffprobe_duration_seconds(audio_path)
    pairs = smart_align(dur, lines, cfg, verbose=verbose)
    write_timings_csv(pairs, out_csv)
    log("ALIGN", f"[smart] Wrote timings → {out_csv}", GREEN)
    return out_csv

# end of aligner.py
