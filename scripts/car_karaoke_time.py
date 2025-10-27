#!/usr/bin/env python3
# car_karaoke_time.py
# Single-call pipeline with:
# - --url download via yt-dlp
# - --resync-offset: fast re-mux only
# - default: open output folder; use --skip-open-dir to suppress
# - --reuse-existing-timings: reuse timings CSV and re-render with updated lyrics
# - Leading-URL-in-lyrics detection: if first line starts with https:// use as URL and ignore it

import argparse, sys, subprocess, shlex, shutil, tempfile
from pathlib import Path

def run(cmd, cwd: Path | None = None):
    print("\n▶", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)

def ensure_dir(p: Path): p.mkdir(parents=True, exist_ok=True)

def open_in_explorer(path: Path):
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass

def derive_base(lyrics_path: Path) -> str:
    return lyrics_path.stem

def sanitize_lyrics_and_detect_url(lyrics_path: Path, tmp_dir: Path) -> tuple[Path, str | None]:
    """If first line starts with https:// treat it as URL and strip it from the lyrics."""
    detected_url = None
    with lyrics_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    if lines and lines[0].strip().startswith("https://"):
        detected_url = lines[0].strip()
        stripped = "".join(lines[1:])
        out = tmp_dir / f"{lyrics_path.stem}_sanitized.txt"
        with out.open("w", encoding="utf-8") as g:
            g.write(stripped)
        return out, detected_url
    return lyrics_path, None

def build_args():
    ap = argparse.ArgumentParser(description="Car Karaoke pipeline runner")
    ap.add_argument("--repo-root", default=".", help="Repo root where scripts/ lives")
    ap.add_argument("--lyrics", required=True, help="Path to lyrics .txt")
    ap.add_argument("--audio", help="Path to song audio (e.g., .mp3)")
    ap.add_argument("--url", help="YouTube URL. If given and --audio not set, downloads MP3 via yt-dlp")
    ap.add_argument("--timings", help="Existing timings CSV. If absent, will generate (unless --seconds-per-slide).")
    ap.add_argument("--seconds-per-slide", type=float, help="Used only if --timings not given")
    ap.add_argument("--offset-video", type=float, default=0.0,
                    help="Seconds to delay the VIDEO vs AUDIO during mux. Positive delays video.")
    ap.add_argument("--resync-offset", type=float,
                    help="Shortcut: reuse existing render and only re-mux with this offset. Implies --mux-only.")
    ap.add_argument("--reuse-existing-timings", action="store_true",
                    help="Reuse existing timings CSV and re-render with updated lyrics, then mux.")
    # Back-compat alias (hidden)
    ap.add_argument("--rerender-lyrics", action="store_true", help=argparse.SUPPRESS)

    ap.add_argument("--font-size", type=int, default=100)
    ap.add_argument("--last-slide-hold", type=float, default=3.0)
    ap.add_argument("--aac-kbps", type=int, default=192)
    ap.add_argument("--remove-cache", action="store_true")
    ap.add_argument("--skip-open-dir", action="store_true",
                    help="Do not open the output folder when finished")
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

    # Apply aliases and shortcuts
    if args.rerender_lyrics:
        args.reuse_existing_timings = True
    if args.resync_offset is not None:
        args.offset_video = args.resync_offset
        args.mux_only = True

    repo_root = Path(args.repo_root).resolve()
    scripts_dir = repo_root / "scripts"

    lyrics_src_path = Path(args.lyrics).resolve()
    if not lyrics_src_path.exists():
        print("ERROR: --lyrics not found:", lyrics_src_path); sys.exit(2)

    # Temp dir for sanitized lyrics if needed
    tmp_dir = Path(tempfile.gettempdir()) / "car_karaoke_time_tmp"
    ensure_dir(tmp_dir)
    lyrics_path, detected_url = sanitize_lyrics_and_detect_url(lyrics_src_path, tmp_dir)

    # Prefer explicit --url, else use detected URL in first line of lyrics
    if not args.url and detected_url:
        print(f"Info: detected URL in first line of lyrics, will use it: {detected_url}")
        args.url = detected_url

    base = args.basename or derive_base(lyrics_src_path)  # base from original name

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
    if (not args.mux_only
        and not (scripts_dir / "make_timing_csv.py").exists()
        and args.timings is None
        and not args.reuse_existing_timings):
        print("WARN: scripts/make_timing_csv.py not found; will proceed only if --timings provided or --seconds-per-slide used, or --reuse-existing-timings specified.")

    # Audio selection
    audio_path = Path(args.audio).resolve() if args.audio else None
    if audio_path and not audio_path.exists():
        print("ERROR: --audio not found:", audio_path); sys.exit(6)

    # Auto-download from URL if needed (not in mux-only)
    if not audio_path and args.url and not args.mux_only:
        if shutil.which("yt-dlp") is None:
            print("ERROR: yt-dlp not found on PATH but --url was provided"); sys.exit(7)
        audio_path = songs_dir / f"{base}.mp3"
        ytdlp_cmd = ["yt-dlp", "-x", "--audio-format", "mp3",
                     "-o", str(songs_dir / f"{base}.%(ext)s"), args.url]

    # In mux-only or resync mode, infer audio if not given
    if args.mux_only and not audio_path:
        candidate = songs_dir / f"{base}.mp3"
        if candidate.exists():
            audio_path = candidate
        else:
            print("ERROR: audio not found. Provide --audio or place", candidate); sys.exit(8)

    if not args.render_only and not audio_path:
        print("ERROR: need --audio or --url"); sys.exit(5)

    # If re-muxing, ensure the rendered MP4 exists
    if args.mux_only and not rendered_mp4.exists():
        print("ERROR: expected rendered video missing:", rendered_mp4)
        print("Run a render first without --mux-only.")
        sys.exit(9)

    cmds = []

    # Download first if planned and file not yet present
    if not args.render_only and args.url and audio_path and not audio_path.exists():
        cmds.append(ytdlp_cmd)

    # Step 1: timings
    # - Normal: generate if not provided and no fixed seconds-per-slide
    # - --reuse-existing-timings: skip timings generation but require an existing timings CSV
    need_timings = False
    if args.reuse_existing_timings:
        if not timings_csv.exists():
            print("ERROR: --reuse-existing-timings requires existing timings CSV at", timings_csv)
            sys.exit(10)
    elif not args.mux_only:
        need_timings = args.timings is None and args.seconds_per_slide is None
        if need_timings:
            mtc = [
                sys.executable, str(scripts_dir / "make_timing_csv.py"),
                "--lyrics", str(lyrics_path),
                "--audio", str(audio_path),
                "--out", str(timings_csv),
            ]
            cmds.append(mtc)

    # Step 2: render (skip when mux-only). Always use possibly-sanitized lyrics_path.
    if not args.mux_only:
        krc = [
            sys.executable, str(scripts_dir / "karaoke_render_chrome.py"),
            "--lyrics", str(lyrics_path),
            "--font-size", str(args.font_size),
        ]
        if args.remove_cache:
            krc.append("--remove-cache")
        # Use timings either from existing file (reuse-existing) or newly generated, else fixed duration
        if args.reuse_existing_timings or args.timings or need_timings:
            krc += ["--timings", str(timings_csv), "--last-slide-hold", str(args.last_slide_hold)]
        else:
            if args.seconds_per_slide is None:
                print("ERROR: provide --timings or --seconds-per-slide"); sys.exit(7)
            krc += ["--seconds-per-slide", str(args.seconds_per_slide)]
        cmds.append(krc)

    # Step 3: mux
    if not args.render_only:
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
    if not args.mux_only: print(" - Rendered MP4:", rendered_mp4)
    if not args.render_only: print(" - Muxed MP4   :", muxed_mp4)
    if (args.reuse_existing_timings or args.timings or need_timings) and not args.mux_only:
        print(" - Timings CSV :", timings_csv)
    if (args.url and not args.mux_only) or (args.mux_only and audio_path):
        print(" - Audio MP3   :", audio_path)

    if args.dry_run:
        print("\nDry-run. No commands executed.")
    else:
        for c in cmds: run(c)
        print("\nDone.")
        if not args.render_only:
            print("Final:", muxed_mp4)

    # Default: open outdir unless explicitly skipped
    if not args.skip_open_dir:
        open_in_explorer(outdir)

if __name__ == "__main__":
    main()

# end of car_karaoke_time.py
