#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, re, subprocess, sys, time
from pathlib import Path

def ensure_dir(p: Path): p.mkdir(parents=True, exist_ok=True)
def sanitize_basename(p: Path) -> str:
    import re; return re.sub(r"[^A-Za-z0-9_-]+", "_", p.stem).strip("_") or "song"

def split_screens(raw_text: str):
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    out = []
    for ln in lines:
        s = ln.replace(r"\/", "\uE000")
        s = re.sub(r"/{1,}", lambda m: "\n" * len(m.group(0)), s)
        s = s.replace("\uE000", "/").strip("\n")
        out.append(s)
    return out

def write_timing_csv(path: Path, lines, starts):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["line","start"])
        for ln, st in zip(lines, starts): w.writerow([ln, f"{st:.3f}"])

def which(cmd):
    from shutil import which as _which; return _which(cmd)

def start_audio(audio_path: Path):
    cands = []
    if which("afplay"): cands = [["afplay", str(audio_path)]]
    elif which("ffplay"): cands = [["ffplay","-nodisp","-autoexit","-loglevel","quiet",str(audio_path)]]
    elif which("mpg123"): cands = [["mpg123","-q",str(audio_path)]]
    elif which("aplay"): cands = [["aplay", str(audio_path)]]
    for cmd in cands:
        try: return subprocess.Popen(cmd)
        except Exception: continue
    return None

def main():
    ap = argparse.ArgumentParser(description="Interactive tap timing → CSV for slash-format lyrics.")
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    lyr_path = Path(args.lyrics); aud_path = Path(args.audio)
    if not lyr_path.exists(): print(f"FATAL: lyrics not found: {lyr_path}", file=sys.stderr); sys.exit(1)
    if not aud_path.exists(): print(f"FATAL: audio not found: {aud_path}", file=sys.stderr); sys.exit(1)

    raw = lyr_path.read_text(encoding="utf-8")
    screens = split_screens(raw)
    if not screens: print("FATAL: parsed 0 screens.", file=sys.stderr); sys.exit(1)

    out_dir = Path("output") / "timings"; ensure_dir(out_dir)
    csv_path = Path(args.out) if args.out else out_dir / f"{sanitize_basename(lyr_path)}.csv"
    if csv_path.parent: ensure_dir(csv_path.parent)

    print("\n=== Tap Timing Mode ===")
    print("Start audio and press Enter for each NEXT screen.")
    input("Ready? Press Enter to start…")

    proc = start_audio(aud_path)
    if proc is None:
        input("Could not auto-start audio. Start manually, then press Enter to begin…")

    t0 = time.perf_counter(); starts = []
    try:
        for i, text in enumerate(screens, 1):
            print(f"[{i}/{len(screens)}]\n{text}\n")
            input("Tap Enter ➜ ")
            starts.append(time.perf_counter() - t0 + float(args.offset))
    except KeyboardInterrupt:
        if proc:
            try: proc.terminate()
            except Exception: pass
        sys.exit(130)

    if proc:
        try: proc.wait(timeout=1)
        except Exception:
            try: proc.terminate()
            except Exception: pass

    write_timing_csv(csv_path, screens, starts)
    print(f"\n✅ Saved CSV: {csv_path}")

if __name__ == "__main__":
    main()
