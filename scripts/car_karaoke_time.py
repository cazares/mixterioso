#!/usr/bin/env python3
# car_karaoke_time.py
# Single-call pipeline:
# - Detect URL in first line of lyrics, download MP3 via yt-dlp, ignore that line for display
# - Manual timing (or reuse CSV), render video-only once, then mux 3 finals:
#     1) 100% vocals: original MP3, no Demucs, no audio processing besides AAC mux
#     2) 25% vocals: Demucs 6-stem, vocals at 0.25 over instrumental
#     3) 0%  vocals: Demucs 6-stem, instrumental only
# - --resync-offset: fast re-mux path
# - default: open output folder; disable via --skip-open-dir
# - --reuse-existing-timings: reuse CSV after editing lyrics text

import argparse, sys, subprocess, shlex, shutil, tempfile
from pathlib import Path

def run(cmd, cwd: Path | None = None, check: bool = True):
    print("\n▶", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)

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

def derive_base(p: Path) -> str:
    return p.stem

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

def ffprobe_has_audio(path: Path) -> bool:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            stderr=subprocess.STDOUT
        ).decode().strip()
        return bool(out)
    except Exception:
        return False

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
    ap.add_argument("--skip-open-dir", action="store_true", help="Do not open the output folder when finished")
    ap.add_argument("--outdir", default="output/chrome_rendered_mp4s")
    ap.add_argument("--timings-outdir", default="output/timings")
    ap.add_argument("--songs-dir", default="songs", help="Where to place downloaded MP3s")
    ap.add_argument("--basename", help="Override output base name (defaults to lyrics filename)")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--mux-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")

    # Demucs controls (used only for 25% and 0% variants)
    ap.add_argument("--only-100", action="store_true",
                    help="Skip Demucs variants; produce only the full-vocals output")
    ap.add_argument("--demucs-model", default="htdemucs",
                    help="Demucs model name for 6-stem separation")
    ap.add_argument("--demucs-overlap", type=float, default=0.25,
                    help="Demucs overlap fraction (higher = better, slower)")
    ap.add_argument("--demucs-seg", type=int, default=6,
                    help="Demucs segment length seconds (smaller = better, slower)")
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

    base = args.basename or derive_base(lyrics_src_path)   # base from original name (stable for outputs)
    render_base = derive_base(lyrics_path)                  # may be *_sanitized

    outdir = Path(args.outdir).resolve()
    timings_outdir = Path(args.timings_outdir).resolve()
    songs_dir = Path(args.songs_dir).resolve()
    sep_root = repo_root / "separated"  # Demucs default root
    ensure_dir(outdir); ensure_dir(timings_outdir); ensure_dir(songs_dir); ensure_dir(sep_root)

    timings_csv = Path(args.timings).resolve() if args.timings else (timings_outdir / f"{base}.csv")
    rendered_mp4 = outdir / f"{render_base}_chrome_static.mp4"  # video-only render path
    # Final outputs:
    mp4_full   = outdir / f"{base}_chrome_static_with_audio_sync.mp4"          # 100% vocals (unchanged)
    mp4_v25    = outdir / f"{base}_vocals25_chrome_static_with_audio_sync.mp4" # 25% vocals
    mp4_novox  = outdir / f"{base}_no_vocals_chrome_static_with_audio_sync.mp4" # 0% vocals

    # Fallback if sanitized name was used earlier by renderer
    if not rendered_mp4.exists():
        alt = outdir / f"{base}_sanitized_chrome_static.mp4"
        if alt.exists():
            rendered_mp4 = alt

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
        ytdlp_cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(songs_dir / f"{base}.%(ext)s"), args.url]

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

    # Step 2: render video-only (skip when mux-only). Always use possibly-sanitized lyrics_path.
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

    # --- Step 3: mux 100% vocals (unchanged) ---
    if not args.render_only:
        ff_full = [
            "ffmpeg", "-y",
            "-itsoffset", str(args.offset_video),
            "-i", str(rendered_mp4),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
            "-shortest", "-movflags", "+faststart",
            str(mp4_full),
        ]
        cmds.append(ff_full)

    # --- Demucs-based variants (skip if --only-100 or mux-only) ---
    if not args.only_100 and not args.mux_only:
        if shutil.which("demucs") is None:
            print("ERROR: demucs not found on PATH but Demucs variants requested"); sys.exit(11)

        # Demucs separation
        stems_out = sep_root  # demucs will create separated/<model>/<base>/*
        demucs_cmd = [
            "demucs",
            "-n", args.demucs_model,
            "--overlap", str(args.demucs_overlap),
            "--segment", str(args.demucs_seg),
            "-o", str(stems_out),
            str(audio_path),
        ]
        cmds.append(demucs_cmd)

        # Paths to stems
        stems_dir = stems_out / args.demucs_model / base
        vocals_wav  = stems_dir / "vocals.wav"
        drums_wav   = stems_dir / "drums.wav"
        bass_wav    = stems_dir / "bass.wav"
        other_wav   = stems_dir / "other.wav"
        guitar_wav  = stems_dir / "guitar.wav"
        piano_wav   = stems_dir / "piano.wav"

        # Build instrumental: sum all non-vocal stems
        instrumental_wav = stems_dir / f"{base}_instrumental_mix.wav"
        # Use amix with equal weights, normalization off to preserve headroom
        ff_instr = [
            "ffmpeg", "-y",
            "-i", str(drums_wav), "-i", str(bass_wav), "-i", str(other_wav),
            "-i", str(guitar_wav), "-i", str(piano_wav),
            "-filter_complex",
            "[0:a][1:a][2:a][3:a][4:a]amix=inputs=5:normalize=0[a];[a]dynaudnorm[aout]",
            "-map", "[aout]",
            "-c:a", "pcm_s16le",
            str(instrumental_wav),
        ]
        cmds.append(ff_instr)

        # 0% vocals video: instrumental only
        ff_novox = [
            "ffmpeg", "-y",
            "-itsoffset", str(args.offset_video),
            "-i", str(rendered_mp4),
            "-i", str(instrumental_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
            "-shortest", "-movflags", "+faststart",
            str(mp4_novox),
        ]
        cmds.append(ff_novox)

        # 25% vocals mix: instrumental + vocals * 0.25
        v25_wav = stems_dir / f"{base}_vocal25_mix.wav"
        ff_v25_mix = [
            "ffmpeg", "-y",
            "-i", str(instrumental_wav), "-i", str(vocals_wav),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:weights=1 0.25:normalize=0[aout]",
            "-map", "[aout]",
            "-c:a", "pcm_s16le",
            str(v25_wav),
        ]
        cmds.append(ff_v25_mix)

        ff_v25_mux = [
            "ffmpeg", "-y",
            "-itsoffset", str(args.offset_video),
            "-i", str(rendered_mp4),
            "-i", str(v25_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
            "-shortest", "-movflags", "+faststart",
            str(mp4_v25),
        ]
        cmds.append(ff_v25_mux)

    # Plan
    print("\n=== Plan ===")
    for c in cmds:
        print(" ", " ".join(shlex.quote(x) for x in c))

    print("\nOutputs:")
    if not args.render_only and not args.mux_only:
        print(" - Video-only render:", rendered_mp4)
    print(" - 100% vocals:", mp4_full)
    if not args.only_100 and not args.mux_only:
        print(" - 25% vocals:", mp4_v25)
        print(" - 0%  vocals:", mp4_novox)
    if (args.reuse_existing_timings or args.timings or need_timings) and not args.mux_only:
        print(" - Timings CSV:", timings_csv)
    if (args.url and not args.mux_only) or (args.mux_only and audio_path):
        print(" - Audio MP3  :", audio_path)

    if args.dry_run:
        print("\nDry-run. No commands executed.")
    else:
        for c in cmds:
            run(c)
        print("\nDone.")
        # Sanity: confirm audio present in outputs
        if not ffprobe_has_audio(mp4_full):
            print("WARN: 100% vocals output missing audio stream.")
        if not args.only_100 and not args.mux_only:
            if not ffprobe_has_audio(mp4_v25):
                print("WARN: 25% vocals output missing audio stream.")
            if not ffprobe_has_audio(mp4_novox):
                print("WARN: 0% vocals output missing audio stream.")

    # Default: open outdir unless explicitly skipped
    if not args.skip_open_dir:
        open_in_explorer(outdir)

if __name__ == "__main__":
    main()

# end of car_karaoke_time.py
