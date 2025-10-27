#!/usr/bin/env python3
# scripts/car_karaoke_time.py

import argparse, sys, subprocess, shlex, shutil, tempfile, os, csv
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

def derive_base(p: Path) -> str: return p.stem

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

def csv_to_lyrics(csv_path: Path, tmp_dir: Path) -> Path:
    """
    Build a temporary lyrics.txt from a timings CSV that contains lyric text.
    Heuristic: use last column as the lyric line. Ignore blank lines.
    """
    out = tmp_dir / f"{csv_path.stem}_lyrics_from_csv.txt"
    lines_out = []
    with csv_path.open("r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row: continue
            text = row[-1].strip()
            if not text: continue
            # Handle escaped \N used by some pipelines to mean newline
            text = text.replace("\\N", "\n")
            lines_out.append(text)
    with out.open("w", encoding="utf-8") as g:
        g.write("\n".join(lines_out))
    return out

def ffprobe_has_audio(path: Path) -> bool:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            stderr=subprocess.STDOUT
        ).decode().strip()
        return bool(out)
    except Exception:
        return False

def build_args():
    ap = argparse.ArgumentParser(description="Car Karaoke pipeline runner")
    ap.add_argument("--repo-root", default=".", help="Repo root where scripts/ lives")
    ap.add_argument("--lyrics", help="Path to lyrics .txt (optional if --timings provided)")
    ap.add_argument("--audio", help="Path to song audio (e.g., .mp3)")
    ap.add_argument("--url", help="YouTube URL. If given and --audio not set, downloads MP3 via yt-dlp")
    ap.add_argument("--timings", help="Existing timings CSV. If absent, will generate (unless --seconds-per-slide).")
    ap.add_argument("--seconds-per-slide", type=float, help="Used only if --timings not given")

    ap.add_argument("--offset-video", type=float, default=0.0,
                    help="Seconds to delay VIDEO vs AUDIO during mux. Positive delays video.")
    ap.add_argument("--append-end-duration", type=float, default=3.0,
                    help="Freeze last frame for N seconds. 0 disables.")
    ap.add_argument("--resync-offset", type=float,
                    help="Reuse existing render and only re-mux with this offset. Implies --mux-only.")

    ap.add_argument("--font-size", type=int, default=110)
    ap.add_argument("--last-slide-hold", type=float, default=3.0)
    ap.add_argument("--aac-kbps", type=int, default=192)

    ap.add_argument("--remove-cache", action="store_true",
                    help="Pass --remove-cache to the Chrome renderer (fresh render).")
    ap.add_argument("--keep-intermediates", action="store_true",
                    help="Keep video-only render files; default removes them after mux.")
    ap.add_argument("--skip-open-dir", action="store_true",
                    help="Do not open the output folder.")
    ap.add_argument("--outdir", default="output/chrome_rendered_mp4s")
    ap.add_argument("--timings-outdir", default="output/timings")
    ap.add_argument("--songs-dir", default="songs", help="Where to place downloaded MP3s")
    ap.add_argument("--basename", help="Override output base name (defaults to lyrics filename or timings name)")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--mux-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")

    # Demucs and mixing
    ap.add_argument("--high-quality", action="store_true", help="Use 6-stem Demucs (htdemucs_6s)")
    ap.add_argument("--demucs-model", default="htdemucs",
                    help="Demucs model (default 4-stem: htdemucs). Overridden by --high-quality.")
    ap.add_argument("--demucs-overlap", type=float, default=0.25, help="Demucs overlap (higher = better, slower)")
    ap.add_argument("--demucs-seg", type=int, default=6, help="Demucs segment seconds (Transformer models must be <= ~7.8)")
    ap.add_argument("--vocal-pcts", nargs="*", type=float, help="Space-separated vocal percentages, e.g. --vocal-pcts 0 25 100")
    ap.add_argument("--force-demucs", action="store_true", help="Ignore cached stems and re-run Demucs")
    return ap.parse_args()

def main():
    args = build_args()

    # Flags interplay
    if args.resync_offset is not None:
        args.offset_video = args.resync_offset
        args.mux_only = True
    if args.high_quality:
        args.demucs_model = "htdemucs_6s"
    if "htdemucs" in args.demucs_model and args.demucs_seg > 7:
        args.demucs_seg = 6  # safe for Transformer models

    repo_root = Path(args.repo_root).resolve()
    scripts_dir = repo_root / "scripts"

    # Base name resolution: prefer lyrics, else timings
    if args.lyrics:
        lyrics_src_path = Path(args.lyrics).resolve()
        if not lyrics_src_path.exists():
            print("ERROR: --lyrics not found:", lyrics_src_path); sys.exit(2)
    else:
        lyrics_src_path = None

    timings_csv = Path(args.timings).resolve() if args.timings else None
    if timings_csv and not timings_csv.exists():
        print("ERROR: --timings not found:", timings_csv); sys.exit(2)

    # If no lyrics and no timings, we can't proceed
    if not lyrics_src_path and not timings_csv:
        print("ERROR: provide --lyrics or --timings."); sys.exit(2)

    # Output base
    if args.basename:
        base = args.basename
    else:
        seed_path = lyrics_src_path or timings_csv
        base = derive_base(seed_path)

    # URL-in-first-line for lyrics if present
    tmp_dir = Path(tempfile.gettempdir()) / "car_karaoke_time_tmp"
    ensure_dir(tmp_dir)

    detected_url = None
    if lyrics_src_path:
        lyrics_path, detected_url = sanitize_lyrics_and_detect_url(lyrics_src_path, tmp_dir)
    else:
        lyrics_path = None

    # If timings provided, confirm reuse; if no lyrics, generate lyrics from CSV
    if timings_csv:
        try:
            ans = input(f"Reuse timings at {timings_csv}? [Y/n]: ").strip().lower()
        except EOFError:
            ans = "y"
        if ans in ("n", "no"):
            timings_csv = None  # will regenerate below
        if not lyrics_path:
            lyrics_path = csv_to_lyrics(Path(args.timings), tmp_dir)

    # Prefer explicit --url, else URL detected in lyrics first line
    if not args.url and detected_url:
        print(f"Info: detected URL in first line of lyrics, will use it: {detected_url}")
        args.url = detected_url

    outdir = Path(args.outdir).resolve()
    timings_outdir = Path(args.timings_outdir).resolve()
    songs_dir = Path(args.songs_dir).resolve()
    sep_root = repo_root / "separated"
    ensure_dir(outdir); ensure_dir(timings_outdir); ensure_dir(songs_dir); ensure_dir(sep_root)

    # Determine requested vocal percentages
    vocal_pcts = args.vocal_pcts
    if not vocal_pcts or len(vocal_pcts) == 0:
        try:
            raw = input("Enter vocal percentages separated by spaces (e.g., '0 25 100'): ").strip()
        except EOFError:
            print("ERROR: --vocal-pcts not provided and no stdin available."); sys.exit(12)
        vocal_pcts = [float(x) for x in raw.split()] if raw else []
    if not vocal_pcts:
        print("ERROR: no vocal percentages provided."); sys.exit(13)
    # clamp
    vocal_pcts = [0.0 if p < 0 else 200.0 if p > 200 else p for p in vocal_pcts]

    # Intermediates
    tmp_video_dir = Path(tempfile.mkdtemp(prefix="cktmp_"))
    render_base = derive_base(lyrics_path) if lyrics_path else base
    rendered_mp4 = tmp_video_dir / f"{render_base}_chrome_static.mp4"
    extended_mp4 = tmp_video_dir / f"{render_base}_chrome_static_ext.mp4"
    video_for_mux = rendered_mp4

    # Deps
    if shutil.which("ffmpeg") is None:
        print("ERROR: ffmpeg not found on PATH"); sys.exit(3)
    if not (scripts_dir / "karaoke_render_chrome.py").exists():
        print("ERROR: scripts/karaoke_render_chrome.py not found at", scripts_dir); sys.exit(4)
    if (not args.mux_only
        and not timings_csv
        and not (scripts_dir / "make_timing_csv.py").exists()
        and args.seconds_per_slide is None):
        print("ERROR: need scripts/make_timing_csv.py to auto-generate timings, or provide --timings or --seconds-per-slide.")
        sys.exit(4)

    # Audio source
    audio_path = Path(args.audio).resolve() if args.audio else None
    if audio_path and not audio_path.exists():
        print("ERROR: --audio not found:", audio_path); sys.exit(6)

    # Auto-download
    if not audio_path and args.url and not args.mux_only:
        if shutil.which("yt-dlp") is None:
            print("ERROR: yt-dlp not found on PATH but --url was provided"); sys.exit(7)
        audio_path = songs_dir / f"{base}.mp3"
        ytdlp_cmd = ["yt-dlp", "-x", "--audio-format", "mp3",
                     "-o", str(songs_dir / f"{base}.%(ext)s"), args.url]
    if args.mux_only and not audio_path:
        candidate = songs_dir / f"{base}.mp3"
        if candidate.exists():
            audio_path = candidate
        else:
            print("ERROR: audio not found. Provide --audio or place", candidate); sys.exit(8)
    if not args.render_only and not audio_path:
        print("ERROR: need --audio or --url"); sys.exit(5)

    # === Phase A: Prep ===
    if not args.dry_run:
        # Download if planned and missing
        if not args.render_only and args.url and audio_path and not audio_path.exists():
            run(ytdlp_cmd)

        # Timings
        if not args.mux_only:
            if not timings_csv:
                # Need to generate timings
                if not lyrics_path:
                    print("ERROR: no lyrics available to generate timings."); sys.exit(15)
                need_timings = args.seconds_per_slide is None
                if need_timings:
                    run([
                        sys.executable, str(scripts_dir / "make_timing_csv.py"),
                        "--lyrics", str(lyrics_path),
                        "--audio", str(audio_path),
                        "--out", str(timings_outdir / f"{base}.csv"),
                    ])
                    timings_csv = timings_outdir / f"{base}.csv"

        # Render video-only (renderer writes to outdir; we move to tmp)
        if not args.mux_only:
            render_out_path = outdir / f"{render_base}_chrome_static.mp4"
            krc = [
                sys.executable, str(scripts_dir / "karaoke_render_chrome.py"),
                "--lyrics", str(lyrics_path),
                "--font-size", str(args.font_size),
            ]
            if args.remove_cache:
                krc.append("--remove-cache")
            if timings_csv:
                krc += ["--timings", str(timings_csv), "--last-slide-hold", str(args.last_slide_hold)]
            else:
                if args.seconds_per_slide is None:
                    print("ERROR: provide --timings or --seconds-per-slide"); sys.exit(7)
                krc += ["--seconds-per-slide", str(args.seconds_per_slide)]
            run(krc)
            if render_out_path.exists():
                ensure_dir(tmp_video_dir)
                shutil.move(str(render_out_path), str(rendered_mp4))
            else:
                print("ERROR: expected render missing:", render_out_path); sys.exit(9)

        # Optional end freeze — normalize PTS
        if args.append_end_duration and args.append_end_duration > 0:
            run([
                "ffmpeg", "-y",
                "-i", str(rendered_mp4),
                "-filter:v", f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={args.append_end_duration}",
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(extended_mp4),
            ])
            video_for_mux = extended_mp4
        else:
            video_for_mux = rendered_mp4
    else:
        print("\nDry-run. Plan only.")
        print("Rendered video path will be:", rendered_mp4)

    # === Phase B: Produce requested mixes ===
    # Any pct != 100 => need stems
    need_demucs = any(abs(p - 100.0) > 1e-6 for p in vocal_pcts)
    stems_dir = (repo_root / "separated" / args.demucs_model / base)
    def have_stems(d: Path) -> bool:
        return (d / "vocals.wav").exists() and (d / "drums.wav").exists() and (d / "bass.wav").exists() and (d / "other.wav").exists()

    # 100% first if requested
    if 100.0 in vocal_pcts and not args.render_only:
        out_100 = outdir / f"{base}_vocals_100.mp4"
        run([
            "ffmpeg", "-y",
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            "-itsoffset", str(args.offset_video),
            "-i", str(video_for_mux),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
            "-shortest", "-movflags", "+faststart",
            str(out_100),
        ])
        if not args.skip_open_dir:
            open_in_explorer(outdir)

    # Demucs if needed
    vocals_wav = drums_wav = bass_wav = other_wav = guitar_wav = piano_wav = None
    instrumental_wav = None
    if need_demucs and not args.mux_only:
        if not args.force_demucs and have_stems(stems_dir):
            print(f"Reusing existing stems at {stems_dir}")
        else:
            if shutil.which("demucs") is None:
                print("ERROR: demucs not found and stems missing."); sys.exit(11)
            run([
                "demucs",
                "-n", args.demucs_model,
                "--overlap", str(args.demucs_overlap),
                "--segment", str(args.demucs_seg),
                "-o", str(repo_root / "separated"),
                str(audio_path),
            ])
        vocals_wav  = stems_dir / "vocals.wav"
        drums_wav   = stems_dir / "drums.wav"
        bass_wav    = stems_dir / "bass.wav"
        other_wav   = stems_dir / "other.wav"
        guitar_wav  = stems_dir / "guitar.wav"
        piano_wav   = stems_dir / "piano.wav"

        # Instrumental from available non-vocal stems
        stem_list = [drums_wav, bass_wav, other_wav]
        if guitar_wav.exists(): stem_list.append(guitar_wav)
        if piano_wav.exists():  stem_list.append(piano_wav)

        instrumental_wav = stems_dir / f"{base}_instrumental_mix.wav"
        ff_inputs, fc_inputs = [], []
        for idx, pth in enumerate(stem_list):
            ff_inputs += ["-i", str(pth)]
            fc_inputs.append(f"[{idx}:a]")
        amix = f"{''.join(fc_inputs)}amix=inputs={len(stem_list)}:normalize=0[a]"
        run([
            "ffmpeg", "-y",
            *ff_inputs,
            "-filter_complex", amix + ";[a]dynaudnorm[aout]",
            "-map", "[aout]",
            "-c:a", "pcm_s16le",
            str(instrumental_wav),
        ])

    # For each requested pct, create and mux
    for p in vocal_pcts:
        pct_int = int(round(p))
        out_path = outdir / f"{base}_vocals_{pct_int}.mp4"

        if abs(p - 100.0) <= 1e-6:
            # already produced above or will be skipped if exists
            if out_path.exists(): continue
            if args.mux_only and not args.render_only:
                run([
                    "ffmpeg", "-y",
                    "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
                    "-itsoffset", str(args.offset_video),
                    "-i", str(video_for_mux),
                    "-i", str(audio_path),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
                    "-shortest", "-movflags", "+faststart",
                    str(out_path),
                ])
            continue

        if args.mux_only:
            print(f"ERROR: cannot build {pct_int}% mix in --mux-only without stems."); sys.exit(14)

        if abs(p) <= 1e-6:
            # 0% = instrumental only
            run([
                "ffmpeg", "-y",
                "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
                "-itsoffset", str(args.offset_video),
                "-i", str(video_for_mux),
                "-i", str(instrumental_wav),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
                "-shortest", "-movflags", "+faststart",
                str(out_path),
            ])
            continue

        # p in (0,100): build scaled-vocals mix
        mix_wav = stems_dir / f"{base}_vocal{pct_int}_mix.wav"
        scale = p / 100.0
        run([
            "ffmpeg", "-y",
            "-i", str(instrumental_wav), "-i", str(vocals_wav),
            "-filter_complex", f"[0:a][1:a]amix=inputs=2:weights=1 {scale}:normalize=0[aout]",
            "-map", "[aout]",
            "-c:a", "pcm_s16le",
            str(mix_wav),
        ])
        run([
            "ffmpeg", "-y",
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            "-itsoffset", str(args.offset_video),
            "-i", str(video_for_mux),
            "-i", str(mix_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", f"{args.aac_kbps}k",
            "-shortest", "-movflags", "+faststart",
            str(out_path),
        ])

    # Cleanup intermediates
    if not args.keep_intermediates:
        try:
            shutil.rmtree(tmp_video_dir, ignore_errors=True)
        except Exception:
            pass

    if not args.skip_open_dir:
        open_in_explorer(outdir)

    print("\nDone. Outputs:", " ".join([f"{int(round(p))}%" for p in vocal_pcts]))

if __name__ == "__main__":
    main()

# end of car_karaoke_time.py
