#!/usr/bin/env python3
import os, sys, subprocess
from pathlib import Path

def run(cmd, check=True):
    print("▶️", " ".join(cmd))
    subprocess.run(cmd, check=check)

def ensure_demucs():
    try:
        subprocess.run(["demucs", "-h"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("✅ Demucs found.")
    except FileNotFoundError:
        print("⬇️ Installing Demucs...")
        run([sys.executable, "-m", "pip", "install", "-U", "demucs", "soundfile"])

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 karaoke_maker.py <song.mp3>")
        sys.exit(1)
    song = Path(sys.argv[1]).expanduser().resolve()
    if not song.exists():
        sys.exit(f"❌ File not found: {song}")
    ensure_demucs()
    print(f"🎶 Separating vocals from: {song.name}")
    run(["demucs", str(song)])
    sep_dir = Path("separated/htdemucs") / song.stem
    if not sep_dir.exists():
        sys.exit("❌ Could not find Demucs output folder.")
    stems = {n: sep_dir / f"{n}.wav" for n in ("drums", "bass", "other")}
    for f in stems.values():
        if not f.exists(): sys.exit(f"❌ Missing stem: {f}")
    out = song.with_name(f"{song.stem}_instrumental.mp3")
    print("🎧 Combining non-vocal stems…")
    cmd = ["ffmpeg","-y"]
    [cmd.extend(["-i", str(f)]) for f in stems.values()]
    cmd += ["-filter_complex", f"amix=inputs={len(stems)}:normalize=1,alimiter=limit=0.9", "-qscale:a","2", str(out)]
    run(cmd)
    print(f"✅ Instrumental created: {out}")

if __name__ == "__main__":
    main()
