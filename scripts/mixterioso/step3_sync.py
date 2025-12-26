#!/usr/bin/env python3
"""Step 3: Build canonical timings CSV.

Canonical schema:
  line_index,time_secs,text

Sources (auto-prefer):
1) timings/<slug>.lrc
2) timings/<slug>*.vtt  (captions / auto-captions)

If no source exists, Step3 errors (human intervention required).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .common import IOFlags, Paths, log, should_write, write_csv


_TS_LRC = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
_TS_VTT = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d\d\d)\s+-->\s+")


def _parse_lrc(path: Path) -> List[Tuple[float, str]]:
    out: List[Tuple[float, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip("\ufeff").rstrip()
        if not line:
            continue
        m = _TS_LRC.match(line)
        if not m:
            continue
        mm = int(m.group(1))
        ss = float(m.group(2))
        text = (m.group(3) or "").strip()
        if text:
            out.append((mm * 60.0 + ss, text))

    out.sort(key=lambda x: x[0])

    # De-dup identical consecutive cues at same timestamp
    dedup: List[Tuple[float, str]] = []
    last_t: Optional[float] = None
    for t, txt in out:
        if last_t is not None and abs(t - last_t) < 1e-3 and dedup and dedup[-1][1] == txt:
            continue
        dedup.append((t, txt))
        last_t = t
    return dedup


def _parse_vtt(path: Path) -> List[Tuple[float, str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Tuple[float, str]] = []
    i = 0
    while i < len(lines):
        m = _TS_VTT.match(lines[i].strip())
        if not m:
            i += 1
            continue
        hh, mm, ss, ms = map(int, m.groups())
        t0 = hh * 3600.0 + mm * 60.0 + ss + (ms / 1000.0)
        i += 1

        texts: List[str] = []
        while i < len(lines) and lines[i].strip() != "":
            txt = lines[i].strip()
            # Skip metadata notes; keep actual caption text
            if txt and not txt.startswith("NOTE") and not txt.startswith("Kind:") and not txt.startswith("Language:"):
                texts.append(txt)
            i += 1

        text = " ".join(texts).strip()
        if text:
            out.append((t0, text))
        i += 1

    out.sort(key=lambda x: x[0])
    return out


def choose_vtt_for_slug(paths: Paths, slug: str) -> Path | None:
    candidates = sorted(paths.timings.glob(f"{slug}*.vtt"))
    if not candidates:
        return None

    # Prefer English, then Spanish, then most-recently-modified.
    def score(p: Path) -> tuple[int, int]:
        name = p.name.lower()
        pri = 9
        if name.endswith(".en.vtt") or ".en." in name:
            pri = 0
        elif name.endswith(".es.vtt") or ".es." in name:
            pri = 1
        return (pri, -int(p.stat().st_mtime))

    return sorted(candidates, key=score)[0]


def step3_sync(paths: Paths, *, slug: str, flags: IOFlags) -> str:
    csv_path = paths.timings / f"{slug}.csv"
    if csv_path.exists() and not should_write(csv_path, flags, label="timings_csv"):
        log("SYNC", f"Reusing timings CSV: {csv_path}")
        return "csv"

    lrc_path = paths.timings / f"{slug}.lrc"
    rows: List[Tuple[float, str]] = []
    source = "none"

    if lrc_path.exists():
        rows = _parse_lrc(lrc_path)
        source = "lrc"

    if not rows:
        vtt = choose_vtt_for_slug(paths, slug)
        if vtt is not None and vtt.exists():
            rows = _parse_vtt(vtt)
            source = "vtt"

    if not rows:
        raise RuntimeError(f"No LRC/VTT timings found for slug={slug} in {paths.timings}")

    csv_rows: List[List[str]] = []
    for idx, (t, txt) in enumerate(rows):
        csv_rows.append([str(idx), f"{t:.3f}", txt])

    write_csv(csv_path, ["line_index", "time_secs", "text"], csv_rows, flags, label="timings_csv")
    log("SYNC", f"Built timings CSV from {source}: {csv_path}")
    return source


# end of step3_sync.py
