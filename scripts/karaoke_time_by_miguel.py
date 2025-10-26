#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_by_miguel.py
Main driver:
- parses args
- builds timing / ASS / etc (legacy mode)
- OR runs chrome-static-slides pipeline for emoji-perfect big text screens
"""

import subprocess
from pathlib import Path
import karaoke_core as kc
import karaoke_audio_video as kav

# we do NOT import karaoke_render_chrome as a module
# instead we shell out to it so Chromium path etc stays contained there.

def main():
    args = kc.build_arg_parser().parse_args()

    kc.ensure_bins(require_demucs=not args.skip_demucs)

    lyrics_path = Path(args.lyrics).expanduser().resolve()
    audio_arg = args.audio

    # allow YouTube URLs in --audio (legacy behavior)
    if audio_arg.startswith("http://") or audio_arg.startswith("https://"):
        audio_path, song_base, out_dir = kc.handle_youtube_download(audio_arg, lyrics_path)
    else:
        audio_path = Path(audio_arg).expanduser().resolve()
        song_base = kc.song_base_from_path(lyrics_path)
        out_dir = Path("output") / song_base
        kc.ensure_dir(out_dir)

    # Step 1. Ask stems / demucs mix like before
    stem_plan = kav.interactive_stem_selection(no_prompt=args.no_prompt)
    # detect if user actually changed levels from default 100s
    any_change = any(v != 100 for v in stem_plan.all_levels.values())
    final_audio = kav.run_demucs_if_needed(
        audio_path=audio_path,
        args=args,
        out_dir=out_dir,
        stem_plan=stem_plan,
        any_change=any_change
    )

    ##############################################
    # BRANCH A: chrome-static-slides mode (new)  #
    ##############################################

    if args.chrome_static_slides:
        kc.info("🖼 chrome-static-slides mode requested.")
        # 1. call karaoke_render_chrome.py to build frames_chrome/*.png
        #    we pass --lyrics and --font-size
        kc.ensure_dir(Path("output/frames_chrome"))
        chrome_cmd = [
            "python3",
            "karaoke_render_chrome.py",
            "--lyrics", str(lyrics_path),
            "--font-size", str(args.chrome_font_size),
        ]
        kc.run(chrome_cmd, check=True)

        # 2. stitch frames + final_audio into mp4
        mp4_path = kav.chrome_static_to_mp4(
            final_audio_path=final_audio,
            song_base=song_base,
            seconds_per_frame=1.5,
            fps_visual=args.fps if hasattr(args, "fps") else 30,
        )
        kc.info(f"✅ Chrome static karaoke video ready: {mp4_path}")
        return

    ######################################################
    # BRANCH B: legacy ASS karaoke mode (existing logic) #
    ######################################################

    # Read lyrics lines and maybe timing CSV or interactively tap
    lines = kc.read_text_lines(lyrics_path)

    # If CSV provided, reuse timing
    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            kc.die(f"CSV not found: {csv_path}")
        lines_for_ass, starts = kc.read_timing_csv(csv_path)
    else:
        # interactive tap timing
        kc.info("⏱  No CSV provided. Enter timing interactively.")
        starts = kc.tap_to_time(lines)
        csv_auto = out_dir / f"{song_base}_timing.csv"
        kc.write_timing_csv(csv_auto, lines, starts)

    # build ASS subtitles
    ass_path = Path(args.ass) if args.ass else out_dir / f"{song_base}_subtitles.ass"

    # parse resolution
    w_str, h_str = args.resolution.split("x")
    w, h = int(w_str), int(h_str)

    kc.write_ass(
        path=ass_path,
        w=w,
        h=h,
        size=args.font_size,
        lines=lines,
        starts=starts,
        offset=args.offset,
        hold=args.line_hold,
    )

    # now call the legacy render function in karaoke_audio_video
    # In your previous code this probably did ffmpeg with libass overlay and emoji PNGs etc.
    # We keep your existing render function name if you had it, or we do a minimal inline.
    # We'll do minimal fallback here:
    final_mp4 = out_dir / f"{song_base}_karaoke.mp4"

    # Minimal "legacy" ffmpeg call that burns ASS:
    # Background color -> ASS -> map final_audio
    # This is simplified but follows the spirit of your older render_karaoke_video.
    fc = (
        f"color=c={args.bg_color}:s={args.resolution}:r={args.fps}[bg];"
        f"[bg]ass={ass_path}:fontsdir=assets[v0]"
    )
    cmd = [
        "ffmpeg","-y",
        "-i", str(final_audio),
        "-filter_complex", fc,
        "-map","[v0]",
        "-map","0:a",
        "-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","192k",
        "-shortest",
        "-movflags","+faststart",
        str(final_mp4),
    ]
    kc.run(cmd, check=True)

    kc.info(f"✅ Timing-synced karaoke video ready: {final_mp4}")


if __name__ == "__main__":
    main()
