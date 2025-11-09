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

def sync_lyrics_into_csv(csv_path: Path, lyrics_path: Path):
    """
    Copy text from lyrics file into CSV column 'line' (column 0).
    Do not touch 'start' times. Treat '/' as a newline for on-screen wrapping.
    Ignore a first-line URL.
    """
    # Read CSV
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0][:2] != ["line", "start"]:
        print("FATAL: timings CSV must have headers: line,start")
        sys.exit(2)

    # Read lyrics
    txt = lyrics_path.read_text(encoding="utf-8").splitlines()
    if txt and txt[0].strip().startswith("https://"):
        txt = txt[1:]
    txt = [t.replace("/", "\n") for t in txt]

    n = min(len(txt), len(rows) - 1)
    for i in range(1, 1 + n):
        # Ensure row has at least two columns
        if len(rows[i]) < 2:
            # pad missing fields; if totally empty, line="", start="0"
            line_val = rows[i][0] if rows[i] else ""
            start_val = rows[i][1] if len(rows[i]) > 1 else "0"
            rows[i] = [line_val, start_val]
        # Overwrite the 'line' column only
        rows[i][0] = txt[i - 1]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Updated {n} rows in {csv_path}")

def build_args():
    ap = argparse.ArgumentParser(description="Car Karaoke pipeline runner")
    ap.add_argument("--repo-root", default=".", help="Repo root where scripts/ lives")

    # CSV + lyrics
    ap.add_argument("--lyrics", required=True, help="Path to lyrics .txt")
    ap.add_argument("--timings", help="Existing timings CSV. If absent, will generate (unless --seconds-per-slide).")
    ap.add_argument("--reuse-existing-timings", action="store_true",
                    help="Reuse timings CSV and re-render with updated lyrics, then mux.")
    ap.add_argument("--sync-lyrics-into-csv", action="store_true",
                    help="Before render: copy text from --lyrics into column 'line' of --timings.")
    ap.add_argument("--seconds-per-slide", type=float, help="Used only if --timings not given")

    ap.add_argument("--audio", help="Path to song audio (e.g., .mp3)")
    ap.add_argument("--url", help="YouTube URL. If given and --audio not set, downloads MP3 via yt-dlp")

    ap.add_argument("--offset-video", type=float, default=0.0,
                    help="Seconds to delay VIDEO vs AUDIO during mux. Positive delays video.")
    ap.add_argument("--append-end-duration", type=float, default=7.77,
                    help="Freeze last frame for N seconds. 0 disables.")
    ap.add_argument("--resync-offset", type=float,
                    help="Reuse existing render and only re-mux with this offset. Implies --mux-only.")

    ap.add_argument("--font-size", type=int, default=110)
    ap.add_argument("--last-slide-hold", type=float, default=7.77)
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
    ap.add_argument("--basename", help="Override output base name (defaults to lyrics filename)")
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

    # Required lyrics
    lyrics_src_path = Path(args.lyrics).resolve()
    if not lyrics_src_path.exists():
        print("ERROR: --lyrics not found:", lyrics_src_path); sys.exit(2)

    # URL in first line (optional)
    tmp_dir = Path(tempfile.gettempdir()) / "car_karaoke_time_tmp"
    ensure_dir(tmp_dir)
    lyrics_path, detected_url = sanitize_lyrics_and_detect_url(lyrics_src_path, tmp_dir)
    if not args.url and detected_url:
        print(f"Info: detected URL in first line of lyrics, will use it: {detected_url}")
        args.url = detected_url

    base = args.basename or derive_base(lyrics_src_path)   # stable for outputs
    render_base = derive_base(lyrics_path)                  # may be *_sanitized

    outdir = Path(args.outdir).resolve()
    timings_outdir = Path(args.timings_outdir).resolve()
    songs_dir = Path(args.songs_dir).resolve()
    sep_root = repo_root / "separated"
    ensure_dir(outdir); ensure_dir(timings_outdir); ensure_dir(songs_dir); ensure_dir(sep_root)

    # Vocal %s
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
    timings_csv = Path(args.timings).resolve() if args.timings else (timings_outdir / f"{base}.csv")
    rendered_mp4 = tmp_video_dir / f"{render_base}_chrome_static.mp4"
    extended_mp4 = tmp_video_dir / f"{render_base}_chrome_static_ext.mp4"
    video_for_mux = rendered_mp4

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

    # Audio source
    audio_path = Path(args.audio).resolve() if args.audio else None
    if audio_path and not audio_path.exists():
        print("ERROR: --audio not found:", audio_path); sys.exit(6)

    # Auto-infer audio from songs/<base>.mp3 or prompt, else URL
    if not audio_path and not args.url:
        candidate = songs_dir / f"{base}.mp3"
        if candidate.exists():
            audio_path = candidate
        else:
            try:
                user_in = input("Provide audio path or YouTube URL: ").strip()
            except EOFError:
                user_in = ""
            if user_in.startswith("http"):
                args.url = user_in
            elif user_in:
                p = Path(user_in).expanduser().resolve()
                if p.exists():
                    audio_path = p

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
        if args.reuse_existing_timings:
            if not timings_csv.exists():
                print("ERROR: --reuse-existing-timings needs", timings_csv); sys.exit(10)
        elif not args.mux_only:
            need_timings = args.timings is None and args.seconds_per_slide is None
            if need_timings:
                run([
                    sys.executable, str(scripts_dir / "make_timing_csv.py"),
                    "--lyrics", str(lyrics_path),
                    "--audio", str(audio_path),
                    "--out", str(timings_csv),
                ])

        # Optional: sync lyrics text into CSV 'line' column
        if args.sync_lyrics_into_csv:
            if not timings_csv.exists():
                print("ERROR: --sync-lyrics-into-csv requires existing --timings CSV"); sys.exit(16)
            sync_lyrics_into_csv(timings_csv, lyrics_path)

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
            if args.reuse_existing_timings or args.timings or (args.timings is None and args.seconds_per_slide is None):
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
    need_demucs = any(abs(p - 100.0) > 1e-6 for p in vocal_pcts)

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
        if not ffprobe_has_audio(out_100):
            print("WARN: vocals_100 output missing audio stream.")
        if not args.skip_open_dir:
            open_in_explorer(outdir)

    # Demucs for non-100, with reuse of cached stems
    vocals_wav = drums_wav = bass_wav = other_wav = guitar_wav = piano_wav = None
    instrumental_wav = None

    # NOTE: stems dir is based on AUDIO stem (matches Demucs output), not lyrics base.
    stems_base = derive_base(audio_path) if audio_path else base
    stems_dir = sep_root / args.demucs_model / stems_base

    def have_stems(d: Path) -> bool:
        return (d / "vocals.wav").exists() and (d / "drums.wav").exists() and (d / "bass.wav").exists() and (d / "other.wav").exists()

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
                "-o", str(sep_root),
                str(audio_path),
            ])
        vocals_wav  = stems_dir / "vocals.wav"
        drums_wav   = stems_dir / "drums.wav"
        bass_wav    = stems_dir / "bass.wav"
        other_wav   = stems_dir / "other.wav"
        guitar_wav  = stems_dir / "guitar.wav"
        piano_wav   = stems_dir / "piano.wav"

        # Build instrumental from available non-vocal stems
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
            if out_path.exists(): 
                continue
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

    print("\nDone. Outputs requested:", " ".join([f"{int(round(p))}%" for p in vocal_pcts]))

if __name__ == "__main__":
    main()

# end of car_karaoke_time.py
