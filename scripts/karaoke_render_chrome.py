#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import html
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import List, Tuple, Optional

# ---------- args ----------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render emoji-safe lyric slides with headless Chrome.")
    p.add_argument("--lyrics", required=True, help="Path to UTF-8 .txt lyrics file (slash format: '/' => \\n)")
    p.add_argument("--font-size", type=int, default=100, help="Base font size px")
    p.add_argument("--width", type=int, default=1920, help="Output width")
    p.add_argument("--height", type=int, default=1080, help="Output height")
    p.add_argument("--seconds-per-slide", type=float, default=1.5, help="Fallback seconds per slide if no CSV")
    p.add_argument("--timings", type=str, default=None, help="CSV with columns: line,start")
    p.add_argument("--last-slide-hold", type=float, default=2.5, help="Hold for last slide if CSV used")
    p.add_argument("--frames-dir", default="output/frames_chrome", help="Directory for PNG frames")
    p.add_argument("--out-mp4-dir", default="output/chrome_rendered_mp4s", help="Directory for final MP4")
    p.add_argument("--remove-cache", action="store_true", help="Delete prior frames/MP4 before rendering")
    return p.parse_args()

# ---------- utils ----------

def fail(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr); sys.exit(1)

def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check)

def which_browser() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    fail("Chrome/Chromium not found.")
    return ""

def ensure_dirs(*dirs: str) -> None:
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)

def cleanup(paths: List[Path]) -> None:
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try: p.unlink()
            except Exception: pass

def sanitize_basename(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", p.stem).strip("_") or "song"

# ---------- parsing ----------

def split_screens_slash(raw_text: str) -> List[str]:
    """
    One screen per non-empty line.
    Inside a screen: "/" group => that many '\n' ("/" -> \n, "//" -> \n\n, ...).
    Literal slash: '\/'.
    """
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]

    out = []
    for ln in lines:
        s = ln.replace(r"\/", "\uE000")
        s = re.sub(r"/{1,}", lambda m: "\n" * len(m.group(0)), s)
        s = s.replace("\uE000", "/").strip("\n")
        out.append(s)
    return out

def load_timings_csv(csv_path: Path) -> List[Tuple[str, float]]:
    rows: List[Tuple[str,float]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if "line" not in r.fieldnames or "start" not in r.fieldnames:
            fail("timings CSV must have headers: line,start")
        for row in r:
            line = row["line"]
            try:
                st = float(row["start"])
            except Exception:
                continue
            rows.append((line, st))
    if not rows:
        fail("timings CSV had no usable rows.")
    return rows

def compute_durations_from_starts(starts: List[float], last_hold: float) -> List[float]:
    durs: List[float] = []
    n = len(starts)
    for i in range(n):
        if i < n-1:
            d = max(0.01, starts[i+1] - starts[i])
        else:
            d = max(0.01, last_hold)
        durs.append(d)
    return durs

# ---------- HTML ----------

def screen_to_html(screen_text: str, font_px: int) -> str:
    safe = html.escape(screen_text).replace("\n", "<br/>")
    css = (
        "html,body{margin:0;padding:0;width:100%;height:100%;background:#000}"
        ".container{width:100vw;height:100vh;display:flex;align-items:center;justify-content:center}"
        f".content{{color:#fff;font-size:{font_px}px;line-height:1.25;text-align:center;"
        "white-space:pre-wrap;overflow-wrap:anywhere;word-wrap:break-word;word-break:normal;"
        "letter-spacing:0;word-spacing:normal;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;"
        "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Helvetica,Arial,"
        "'Apple Color Emoji','Noto Color Emoji','Segoe UI Emoji',sans-serif;"
        "max-width:90vw;max-height:90vh}}"
    )
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{css}</style>"
        "<div class='container'><div class='content'>"
        f"{safe}"
        "</div></div>"
    )

# ---------- render ----------

def render_screens_to_pngs(
    screens: List[str],
    browser_bin: str,
    frames_dir: Path,
    width: int,
    height: int,
    font_size: int,
) -> None:
    ensure_dirs(str(frames_dir))
    for idx, text in enumerate(screens, 1):
        html_doc = screen_to_html(text, font_size)
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html_doc)
            tmp_path = Path(tmp.name)
        out_png = frames_dir / f"{idx:04d}.png"
        cmd = [
            browser_bin, "--headless=new", "--disable-gpu",
            f"--window-size={width},{height}",
            "--hide-scrollbars",
            f"--screenshot={out_png}",
            f"file://{tmp_path}",
        ]
        run(cmd)
        try: tmp_path.unlink()
        except Exception: pass

# ---------- encoding ----------

def write_ffconcat(
    frames_dir: Path,
    durations: List[float],
) -> Path:
    files = sorted(frames_dir.glob("*.png"))
    if not files:
        fail(f"No frames found in {frames_dir}")
    if len(durations) != len(files):
        m = min(len(durations), len(files))
        durations = durations[:m]
        files = files[:m]
    concat_path = frames_dir / "slides.ffconcat"
    with concat_path.open("w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for i, (fp, dur) in enumerate(zip(files, durations)):
            f.write(f"file '{fp.resolve()}'\n")
            f.write(f"duration {max(0.01, float(dur)):.6f}\n")
        # repeat last file once to honor its duration
        f.write(f"file '{files[-1].resolve()}'\n")
    return concat_path

def encode_mp4_concat(
    concat_file: Path,
    out_mp4_dir: Path,
    basename: str,
    width: int,
    height: int,
) -> Path:
    ensure_dirs(str(out_mp4_dir))
    out_mp4 = out_mp4_dir / f"{basename}.mp4"
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )
    cmd = [
        "ffmpeg", "-y",
        "-safe", "0",
        "-f", "concat",
        "-i", str(concat_file),
        "-vf", vf,
        "-c:v", "libx264",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    run(cmd)
    return out_mp4

def encode_mp4_constant_rate(
    frames_dir: Path,
    out_mp4_dir: Path,
    basename: str,
    sec_per_slide: float,
    width: int,
    height: int,
) -> Path:
    ensure_dirs(str(out_mp4_dir))
    out_mp4 = out_mp4_dir / f"{basename}.mp4"
    files = sorted(frames_dir.glob("*.png"))
    if not files:
        fail(f"No frames found in {frames_dir}")

    fr = Fraction.from_float(1.0 / max(0.1, float(sec_per_slide))).limit_denominator(1000)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )
    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"{fr.numerator}/{fr.denominator}",
        "-pattern_type", "glob",
        "-i", str(frames_dir / "*.png"),
        "-vf", vf,
        "-c:v", "libx264",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    run(cmd)
    return out_mp4

# ---------- main ----------

def main() -> None:
    args = parse_args()

    lyrics_path = Path(args.lyrics)
    if not lyrics_path.exists():
        alt = Path(__file__).resolve().parent.parent / args.lyrics
        if alt.exists():
            lyrics_path = alt
        else:
            fail(f"lyrics file not found: {args.lyrics}")

    frames_dir = Path(args.frames_dir)
    out_mp4_dir = Path(args.out_mp4_dir)

    if args.remove_cache:
        cleanup([frames_dir, out_mp4_dir])

    ensure_dirs(str(frames_dir), str(out_mp4_dir))

    raw_text = lyrics_path.read_text(encoding="utf-8")
    screens = split_screens_slash(raw_text)
    if not screens:
        fail("No screens parsed from lyrics.")

    browser = which_browser()

    # render stills
    render_screens_to_pngs(
        screens=screens,
        browser_bin=browser,
        frames_dir=frames_dir,
        width=args.width,
        height=args.height,
        font_size=args.font_size,
    )

    base = sanitize_basename(lyrics_path) + "_chrome_static"

    # durations: CSV or constant
    if args.timings:
        csv_path = Path(args.timings)
        if not csv_path.exists():
            alt_csv = Path("output/timings") / (sanitize_basename(lyrics_path) + ".csv")
            if alt_csv.exists():
                csv_path = alt_csv
            else:
                fail(f"timings CSV not found: {args.timings}")

        rows = load_timings_csv(csv_path)
        # assume rows are in correct order; trim to available screens
        starts = [st for (_, st) in rows][:len(screens)]
        if not starts:
            fail("timings CSV had no starts.")
        durations = compute_durations_from_starts(starts, last_hold=float(args.last_slide_hold))
        concat_file = write_ffconcat(frames_dir, durations)
        out_mp4 = encode_mp4_concat(concat_file, out_mp4_dir, base, args.width, args.height)
    else:
        out_mp4 = encode_mp4_constant_rate(
            frames_dir=frames_dir,
            out_mp4_dir=out_mp4_dir,
            basename=base,
            sec_per_slide=float(args.seconds-per-slide) if hasattr(args, "seconds-per-slide") else float(args.seconds_per_slide),
            width=args.width,
            height=args.height,
        )

    print(f"✅ Done: {out_mp4}")

if __name__ == "__main__":
    main()
