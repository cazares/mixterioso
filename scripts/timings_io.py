#!/usr/bin/env python3
# scripts/timings_io.py
from __future__ import annotations
import csv
from pathlib import Path
from typing import Iterable, List, Tuple, Any

Triple = Tuple[int, float, str]  # (line_index, time_secs, text)
CANON = ["line_index", "time_secs", "text"]


def _coerce_str(x: Any) -> str:
    if isinstance(x, (list, tuple)):
        return " ".join(_coerce_str(y) for y in x)
    return "" if x is None else str(x)


def _parse_time_float(s: str) -> float:
    """
    Accepts plain floats ('1.234') and MM:SS(.mmm) ('01:23.456').
    Falls back to 0.0 on failure.
    """
    s = (s or "").strip()
    if not s:
        return 0.0
    # MM:SS(.mmm)
    if ":" in s:
        try:
            mm, ss = s.split(":", 1)
            return int(mm) * 60 + float(ss)
        except Exception:
            pass
    try:
        return float(s)
    except Exception:
        return 0.0


def load_timings_any(csv_path: Path) -> List[Triple]:
    """
    Load timings from CSV accepting either:
      - canonical: line_index,time_secs,text
      - legacy:   line,start  OR text,start  OR text,time
    Also tolerates extra columns (DictReader puts them under key None as a list),
    duplicate headers, BOMs, and odd casing.
    Returns a list of (line_index, time_secs, text) sorted by time then index,
    with indices re-sequenced to 0..N-1 in timeline order.
    """
    rows: List[Triple] = []
    if not csv_path.exists():
        return rows

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        fieldnames = [h for h in (rdr.fieldnames or []) if h is not None]
        hdr_lower = [h.strip().lower() for h in fieldnames]

        # Header detection (order-insensitive for legacy)
        canon = {"line_index", "time_secs", "text"}.issubset(set(hdr_lower))
        legacy_like = {"line", "start"} <= set(hdr_lower) or \
                      {"text", "start"} <= set(hdr_lower) or \
                      {"text", "time"} <= set(hdr_lower)

        idx_auto = 0
        for raw in rdr:
            # Normalize keys to lowercase strings; keep extras under key None as-is.
            row = { (k.strip().lower() if isinstance(k, str) else k): v
                    for k, v in raw.items() }

            def getv(key: str) -> str:
                return _coerce_str(row.get(key, "")).strip()

            extras = row.get(None, [])  # may be list of extra columns
            extras_str = _coerce_str(extras).strip()

            if canon:
                try:
                    li = int(getv("line_index")) if getv("line_index") != "" else idx_auto
                except Exception:
                    li = idx_auto
                ts = _parse_time_float(getv("time_secs"))
                tx = getv("text") or extras_str
            elif legacy_like:
                # Try common legacy spellings
                tx = getv("line") or getv("text") or extras_str
                ts = _parse_time_float(getv("start") or getv("time"))
                li = idx_auto
            else:
                # Heuristic fallback: flatten values (excluding None key first), then extras
                flat_vals: List[str] = []
                for k, v in row.items():
                    if k is None:
                        continue
                    flat_vals.append(_coerce_str(v).strip())
                if extras_str:
                    flat_vals.append(extras_str)

                # Try to infer (secs, text)
                li = idx_auto
                ts = 0.0
                tx = " ".join([v for v in flat_vals if v])  # default glue
                if len(flat_vals) >= 2:
                    # Prefer first numeric as time, second as text
                    t0 = _parse_time_float(flat_vals[0])
                    t1 = _parse_time_float(flat_vals[1])
                    if t0 > 0 and (t1 == 0 or not flat_vals[1].replace(".", "", 1).isdigit()):
                        ts = t0
                        tx = flat_vals[1]
                    elif t1 > 0:
                        ts = t1
                        tx = flat_vals[0]

            rows.append((li, ts, tx))
            idx_auto += 1

    # Stable sort by time then original index, then re-sequence
    rows.sort(key=lambda t: (t[1], t[0]))
    normalized = [(i, t, x) for i, (_, t, x) in enumerate(rows)]
    return normalized


def save_timings_canonical(csv_path: Path, triples: Iterable[Triple]) -> None:
    """
    Write canonical header/rows: line_index,time_secs,text
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CANON)
        w.writeheader()
        for li, ts, tx in triples:
            w.writerow({"line_index": li, "time_secs": f"{float(ts):.6f}", "text": tx})

# end of scripts/timings_io.py
