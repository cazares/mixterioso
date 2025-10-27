#!/usr/bin/env python3
# ADD: single-call YouTube URL support via --url (uses yt-dlp)

import argparse, sys, subprocess, shlex, shutil
from pathlib import Path

def run(cmd, cwd: Path | None = None):
    print("\n▶", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)

def ensure_dir(p: Path): p.mkdir(parents=True, exist_ok=True)
def derive_base(lyrics_path: Path) -> str: return lyrics_path.stem

def build_args():
    ap = argparse.ArgumentParser(description="Car Karaoke pipeline runner")
    ap.add_argument("--repo-root", default=".", help="Repo root where scripts/ lives")
    ap.add_argument("--lyrics", required=True, help="Path to lyrics .txt")
    ap.add_argument("--audio", help="Path to song audio (e.g., .mp3)")
    ap.add_argument("--url", help="YouTube URL. If given and --audio not set, downloads MP3 via yt-dlp")
    ap.add_argument("--timings", help="Existing timings CSV. If absent, will generate.")
    ap.add_argument("--seconds-per-slide", type=float, help="Used only if --timings not given")
    ap.add_argument("--offset-video", type=float, default=0.0,
                    help="Seconds to delay the VIDEO vs AUDIO during mux. Positive delays video.")
    ap.add_argument("--font-size", type=int, default=100)
    ap.add_argument("--last-slide-hold", type=float, default=3.0)
    ap.add_argument("--aac-kbps", type=int, default=192)
    ap.add_argument("--remove-cache", action="store_true")
    ap.add_argument("--outdir", default="output/chrome_rendered_mp4s")
    ap.add_argument("--timings-outdir", default="output/timings")
    ap.add_argument("--songs-dir", default="songs", help="Where to place downloaded MP3s")
    ap.add_argument("--basename", help="Override output base name (defaults to lyrics filename)")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--mux-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args()

def main():
    args = build_args()
    repo_root = Path(args.repo_root).resolve()
    scripts_dir = repo_root / "scripts"

    lyrics_path = Path(args.lyrics).resolve()
    if not lyrics_path.exists():
        print("ERROR: --lyrics not found:", lyrics_path); sys.exit(2)

    base = args.basename or derive_base(lyrics_path)

    outdir = Path(args.outdir).resolve()
    timings_outdir = Path(args.timings_outdir).resolve()
    songs_dir = Path(args.songs_dir).resolve()
    ensure_dir(outdir); ensure_dir(timings_outdir); ensure_dir(songs_dir)

    timings_csv = Path(args.timings).resolve() if args.timings else (timings_outdir / f"{base}.csv")
    rendered_mp4 = outdir / f"{base}_chrome_static.mp4"
    muxed_mp4 = outdir / f"{base}_chrome_static_with_audio_sync.mp4"

    # Deps
    if shutil.which("ffmpeg") is None:
        print("ERROR: ffmpeg not found on PATH"); sys.exit(3)
    if not (scripts_dir / "karaoke_render_chrome.py").exists():
        print("ERROR: scripts/karaoke_render_chrome.py not found at", scripts_dir); sys.exit(4)
    if not args.mux-only and not (scripts_dir / "make_timing_csv.py").exists() and args.timings is None:
        print("WARN: scripts/make_timing_csv.py not found; will proceed only if --timings provided or --seconds-per-slide used.")

    # NEW: auto-download audio from YouTube if --url given and --audio not set
    audio_path = Path(args.audio).resolve() if args.audio else None
    if audio_path and not audio_path.exists():
        print("ERROR: --audio not found:", audio_path); sys.exit(6)
    if not audio_path and args.url:
        if shutil.which("yt-dlp") is None:
            print("ERROR: yt-dlp not found on PATH but --url was provided"); sys.exit(7)
        audio_path = songs_dir / f"{base}.mp3"
        ytdlp_cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(songs_dir / f"{base}.%(ext)s"), args.url]

    if not args.render-only and not audio_path:
        print("ERROR: need --audio or --url"); sys.exit(5)

    cmds = []

    # If we will need audio and it comes from URL, put download first
    if not args.render-only and args.url and audio_path and not (audio_path.exists()):
        cmds.append(ytdlp_cmd)

    # Step 1: timings
    need_timings = args.timings is None and args.seconds-per-slide is None and not args.mux-only
    if need_timings:
        mtc = [
            sys.executable, str(scripts_dir / "make_timing_csv.py"),
            "--lyrics", str(lyrics_path),
            "--audio", str(audio_path),
            "--out", str(timings_csv),
        ]
        cmds.append(mtc)

    # Step 2: render
    if not args.mux-only:
        krc = [
            sys.executable, str(scripts_dir / "karaoke_render_chrome.py"),
            "--lyrics", str(lyrics_path),
            "--font-size", str(args.font_size),
        ]
        if args.remove-cache: krc.append("--remove-cache")
        if args.timings or need_timings:
            krc += ["--timings", str(timings_csv), "--last-slide-hold", str(args.last_slide_hold)]
        else:
            if args.seconds-per-slide is None:
                print("ERROR: provide --timings or --seconds-per-slide"); sys.exit(7)
            krc += ["--seconds-per-slide", str(args.seconds-per-slide)]
        cmds.append(krc)

    # Step 3: mux
    if not args.render-only:
        ff = [
            "ffmpeg", "-y",
            "-itsoffset", str(args.offset_video),
            "-i", str(rendered_mp4),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
            "-shortest", "-movflags", "+faststart",
            str(muxed_mp4),
        ]
        cmds.append(ff)

    # Plan
    print("\n=== Plan ===")
    for c in cmds: print(" ", " ".join(shlex.quote(x) for x in c))
    print("\nOutputs:")
    if not args.mux-only: print(" - Rendered MP4:", rendered_mp4)
    if not args.render-only: print(" - Muxed MP4   :", muxed_mp4)
    if args.timings or need_timings: print(" - Timings CSV :", timings_csv)
    if args.url: print(" - Audio MP3   :", audio_path)

    if args.dry_run:
        print("\nDry-run. No commands executed."); return

    for c in cmds: run(c)

    print("\nDone.")
    if not args.render-only: print("Final:", muxed_mp4)

if __name__ == "__main__":
    main()

# end of car_karaoke_time.py
