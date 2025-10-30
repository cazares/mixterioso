#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/render_from_csv.py
Wrapper that reuses the existing Karaoke Time pipeline to render MP4s from a timing CSV.
- Reads CSV with columns: line,start. Ignores any extra columns.
- Reuses scripts/car_karaoke_time.py for rendering, Demucs mixing, and muxing.
- Supports multiple vocal percentages in one run (e.g., --vocal-pcts 0 35 100).
- If --lyrics is omitted, derives a temporary lyrics .txt from the CSV's "line" column.
- Assumes macOS by default but works cross-platform if deps exist.
"""

import argparse
import csv
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

def run(cmd: List[str], cwd: Optional[Path] = None) -> int:
    print("\n▶", " ".join(shlex.quote(c) for c in cmd))
    return subprocess.call(cmd, cwd=str(cwd) if cwd else None)

def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def ok(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False

def validate_csv_has_line_start(csv_path: Path) -> None:
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "line" not in r.fieldnames or "start" not in r.fieldnames:
            die("CSV must have headers: line,start")
        # also verify at least one parseable row
        for row in r:
            if row.get("line") is None:
                continue
            try:
                float(row.get("start", ""))
            except Exception:
                continue
            return
    die("CSV had no usable rows with numeric 'start'.")

def derive_temp_lyrics_from_csv(csv_path: Path, tmpdir: Path) -> Path:
    out = tmpdir / f"{csv_path.stem}_auto_lyrics.txt"
    lines: List[str] = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            line = (row.get("line") or "").rstrip()
            if line:
                lines.append(line)
    # One screen per CSV row: newline between screens
    out.write_text("\n".join(lines), encoding="utf-8")
    return out

def find_car_karaoke(repo_root: Path) -> Path:
    # Prefer scripts/car_karaoke_time.py
    p = repo_root / "scripts" / "car_karaoke_time.py"
    if ok(p):
        return p
    # Fallback: next to this wrapper (if user drops both into same folder)
    local = Path(__file__).resolve().parent / "car_karaoke_time.py"
    if ok(local):
        return local
    die("scripts/car_karaoke_time.py not found. Set --repo-root to your repo top or place it next to this file.")

def build_cmd(
    py: str,
    car_script: Path,
    lyrics_txt: Path,
    csv_path: Path,
    audio: Optional[Path],
    vocal_pcts: List[float],
    font_size: int,
    offset_video: float,
    append_end: float,
    high_quality: bool,
    remove_cache: bool,
    extra: List[str],
) -> List[str]:
    cmd: List[str] = [py, str(car_script), "--lyrics", str(lyrics_txt), "--timings", str(csv_path)]
    if audio:
        cmd += ["--audio", str(audio)]
    if vocal_pcts:
        cmd += ["--vocal-pcts"] + [str(x) for x in vocal_pcts]
    cmd += ["--font-size", str(font_size), "--offset-video", str(offset_video), "--append-end-duration", str(append_end)]
    if high_quality:
        cmd.append("--high-quality")
    if remove_cache:
        cmd.append("--remove-cache")
    if extra:
        cmd += extra
    return cmd

def main() -> None:
    ap = argparse.ArgumentParser(description="Render MP4(s) from timing CSV using Karaoke Time pipeline.")
    ap.add_argument("--csv", required=True, help="Timing CSV with headers: line,start. Extra columns are ignored.")
    ap.add_argument("--lyrics", help="Lyrics .txt. If omitted, will derive from CSV 'line' column.")
    ap.add_argument("--audio", help="Audio file (.mp3, .wav, etc). If omitted, car_karaoke_time.py may auto-infer from songs/<base>.mp3.")
    ap.add_argument("--vocal-pcts", nargs="*", type=float, default=[100.0], help="Space-separated vocal percentages, e.g. 0 25 100")
    ap.add_argument("--font-size", type=int, default=140)
    ap.add_argument("--offset-video", type=float, default=-1.0)
    ap.add_argument("--append-end-duration", type=float, default=0.0)
    ap.add_argument("--repo-root", default=".", help="Repo root that contains scripts/car_karaoke_time.py")
    ap.add_argument("--high-quality", action="store_true", help="Use 6-stem Demucs model for higher quality mixes")
    ap.add_argument("--remove-cache", action="store_true", help="Clear cached frames and outputs before rendering")
    ap.add_argument("--open", dest="open_dir", action="store_true", help="Open output directory when done")
    ap.add_argument("--no-open", dest="open_dir", action="store_false")
    ap.set_defaults(open_dir=True)
    ap.add_argument("extra", nargs=argparse.REMAINDER, help="Additional flags passed through to car_karaoke_time.py")
    args = ap.parse_args()

    csv_path = Path(args.csv).resolve()
    if not ok(csv_path):
        die(f"CSV not found: {csv_path}")
    validate_csv_has_line_start(csv_path)

    # Prepare lyrics
    tmpdir_path = Path(tempfile.mkdtemp(prefix="csv2mp4_"))
    lyrics_txt: Path
    if args.lyrics:
        lyrics_txt = Path(args.lyrics).resolve()
        if not ok(lyrics_txt):
            die(f"Lyrics .txt not found: {lyrics_txt}")
    else:
        lyrics_txt = derive_temp_lyrics_from_csv(csv_path, tmpdir_path)

    audio_path: Optional[Path] = None
    if args.audio:
        audio_path = Path(args.audio).resolve()
        if not ok(audio_path):
            die(f"Audio not found: {audio_path}")

    # Locate car_karaoke_time.py
    car_script = find_car_karaoke(Path(args.repo_root).resolve())

    # Build and run
    py_exec = sys.executable
    cmd = build_cmd(
        py=py_exec,
        car_script=car_script,
        lyrics_txt=lyrics_txt,
        csv_path=csv_path,
        audio=audio_path,
        vocal_pcts=args.vocal_pcts,
        font_size=args.font_size,
        offset_video=args.offset_video,
        append_end=args.append_end_duration,
        high_quality=args.high_quality,
        remove_cache=args.remove_cache,
        extra=args.extra,
    )
    rc = run(cmd)
    if rc != 0:
        die(f"car_karaoke_time.py exited with code {rc}", rc)

    # Try to open the output directory if the downstream script prints it. Fallback: open repo root.
    if args.open_dir:
        try:
            if sys.platform == "darwin":
                subprocess.call(["open", str((Path(args.repo_root) / "output").resolve())])
            elif sys.platform.startswith("win"):
                subprocess.call(["explorer", str((Path(args.repo_root) / "output").resolve())])
            else:
                subprocess.call(["xdg-open", str((Path(args.repo_root) / "output").resolve())])
        except Exception:
            pass

if __name__ == "__main__":
    main()

# end of scripts/render_from_csv.py
