#!/usr/bin/env python3
# scripts/vocal_windows.py — quick vocal-activity windows using short-time energy
# Produces meta/<slug>_vocal_windows.json = {"windows":[[start,end],...], "sr":..., "hop_s":..., ...}
import argparse, json, math
from pathlib import Path

import numpy as np
import soundfile as sf

BASE_DIR = Path(__file__).resolve().parent.parent
MP3_DIR  = BASE_DIR / "mp3s"
META_DIR = BASE_DIR / "meta"

def frame_rms(x: np.ndarray, win: int, hop: int) -> np.ndarray:
    if x.ndim == 2:
        x = x.mean(axis=1)
    n = len(x)
    if n < win:
        pad = win - n
        x = np.pad(x, (0, pad))
        n = len(x)
    frames = 1 + (n - win) // hop
    if frames <= 0:
        return np.array([np.sqrt((x**2).mean())], dtype=np.float32)
    out = np.empty(frames, dtype=np.float32)
    for i in range(frames):
        s = i * hop
        e = s + win
        seg = x[s:e]
        out[i] = math.sqrt(float(np.mean(seg * seg)) + 1e-12)
    return out

def smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    k = np.ones(win, dtype=np.float32) / float(win)
    return np.convolve(x, k, mode="same")

def mask_to_windows(mask: np.ndarray, hop_s: float, win_s: float,
                    min_dur: float, min_gap: float, total_dur: float):
    windows = []
    i = 0
    n = len(mask)
    while i < n:
        if not mask[i]:
            i += 1; continue
        # start of a voiced run
        start_f = i
        while i < n and mask[i]:
            i += 1
        end_f = i - 1
        start_t = start_f * hop_s
        end_t   = end_f * hop_s + win_s
        # duration filter
        if (end_t - start_t) >= min_dur:
            windows.append([start_t, min(end_t, total_dur)])
    if not windows:
        return windows
    # merge close windows
    merged = [windows[0]]
    for s, e in windows[1:]:
        if s - merged[-1][1] <= min_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged

def main():
    ap = argparse.ArgumentParser(description="Detect vocal-ish windows via short-time energy")
    ap.add_argument("--slug", help="slug (will look in mp3s/<slug>.mp3 and write meta/<slug>_vocal_windows.json)")
    ap.add_argument("--audio", help="explicit audio path (overrides --slug)")
    ap.add_argument("--hop", type=float, default=0.02, help="hop size seconds (default 0.02)")
    ap.add_argument("--win", type=float, default=0.08, help="window size seconds (default 0.08)")
    ap.add_argument("--smooth", type=float, default=0.20, help="smoothing seconds (default 0.20)")
    ap.add_argument("--min-dur", type=float, default=0.30, help="min window duration seconds (default 0.30)")
    ap.add_argument("--min-gap", type=float, default=0.25, help="merge gaps <= this seconds (default 0.25)")
    ap.add_argument("--thr-percentile", type=float, default=75.0, help="percentile of RMS for threshold (default 75)")
    ap.add_argument("--thr-ratio", type=float, default=0.35, help="min threshold as ratio of max RMS (default 0.35)")
    args = ap.parse_args()

    if not args.audio and not args.slug:
        ap.error("Provide --slug or --audio")

    if args.audio:
        audio_path = Path(args.audio)
        slug = audio_path.stem
    else:
        slug = args.slug
        audio_path = MP3_DIR / f"{slug}.mp3"

    if not audio_path.exists():
        print(f"[vocal_windows] Audio not found: {audio_path}")
        return 1

    x, sr = sf.read(str(audio_path), always_2d=False)
    hop = max(1, int(sr * args.hop))
    win = max(1, int(sr * args.win))
    rms = frame_rms(x, win=win, hop=hop)
    sm = smooth(rms, win=max(1, int(args.smooth / args.hop)))

    thr = max(np.percentile(sm, args.thr_percentile), float(sm.max()) * args.thr_ratio)
    mask = sm >= thr

    total_dur = len(x) / float(sr)
    windows = mask_to_windows(mask, hop_s=args.hop, win_s=args.win,
                              min_dur=args.min_dur, min_gap=args.min_gap,
                              total_dur=total_dur)

    META_DIR.mkdir(parents=True, exist_ok=True)
    outp = META_DIR / f"{slug}_vocal_windows.json"
    payload = {
        "windows": windows,
        "sr": sr,
        "hop_s": args.hop,
        "win_s": args.win,
        "smooth_s": args.smooth,
        "thr_percentile": args.thr_percentile,
        "thr_ratio": args.thr_ratio,
        "audio": str(audio_path),
        "duration": total_dur,
    }
    outp.write_text(json.dumps(payload, indent=2))
    print(f"[vocal_windows] Wrote {outp} windows={len(windows)} duration={total_dur:.2f}s")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
# end of vocal_windows.py
