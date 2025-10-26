#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_by_miguel.py
High-level entrypoint for Karaoke Time by Miguel.
Delegates helper logic to karaoke_core, karaoke_audio_video, and karaoke_emoji.
"""

import re, subprocess, sys
from pathlib import Path
from karaoke_core import *
from karaoke_audio_video import *
import karaoke_emoji  # additive import, required for emoji overlays

def main():
    ap = build_arg_parser()
    args = ap.parse_args()

    lyrics_path = Path(args.lyrics).expanduser().resolve()
    if not lyrics_path.exists():
        die(f"Lyrics file not found: {lyrics_path}")

    audio_path = Path(args.audio).expanduser()
    base = sanitize_basename(audio_path)
    out_dir = Path("output") / base
    ensure_dir(out_dir)

    # --- YouTube handling (auto open folder after download)
    if str(args.audio).startswith(("http://", "https://")):
        audio_path, base, out_dir = handle_youtube_download(args.audio, lyrics_path)

    csv_path  = Path(args.csv).expanduser().resolve() if args.csv else out_dir / f"{base}_timing.csv"
    ass_path  = Path(args.ass).expanduser().resolve() if args.ass else out_dir / f"{base}_subtitles.ass"
    buddy_mp3 = out_dir / f"{base}_buddy_mix.mp3"
    instr_mp3 = out_dir / f"{base}_instrumental.mp3"
    final_mp4 = out_dir / f"{base}_karaoke.mp4"

    ensure_bins(require_demucs=not args.skip_demucs)

    if args.skip_demucs:
        info("🎵 --skip-demucs → using full mix.")
        stem_plan = StemPlan({}, {k:100 for k in STEM_ORDER})
        any_change = False
        target_audio = audio_path
    else:
        stem_plan = interactive_stem_selection(args.no_prompt)
        any_change = any(v != 100 for v in stem_plan.all_levels.values())
        target_audio = buddy_mp3 if any_change else instr_mp3

    print_plan_summary(lyrics_path, audio_path, out_dir, csv_path, ass_path, final_mp4, stem_plan, target_audio)

    if not args.no_prompt and not args.skip_demucs:
        if not yes_no("Proceed with Demucs + mixing? [y/N]: "):
            die("Cancelled before Demucs.")

    if args.dry_run:
        info("Dry-run complete.")
        return

    # --- Demucs separation & mixdown
    mixed_audio_path = run_demucs_if_needed(audio_path, args, out_dir, stem_plan, any_change)

    # --- Timing
    if csv_path.exists():
        lines, starts = read_timing_csv(csv_path)
    else:
        lines = read_text_lines(lyrics_path)
        if not lines:
            die("Lyrics file empty.")
        if args.no_prompt:
            die("No CSV + no-prompt.")
        starts = tap_to_time(lines)
        write_timing_csv(csv_path, lines, starts)

    # --- ASS + video render (emoji-aware)
    w, h = map(int, re.match(r"^(\d+)x(\d+)$", args.resolution).groups())
    write_ass(ass_path, w, h, args.font_size, lines, starts, args.offset, args.line_hold)

    render_karaoke_video(
        mixed_audio_path,
        ass_path,
        final_mp4,
        args.resolution,
        args.fps,
        args.bg_color,
        lines,
        starts,
        args.offset,
        args.font_size
    )

    info(f"{GREEN}✅ Done → {final_mp4}{RESET}")
    ch = input("Open output folder or video? [f/v/n]: ").lower().strip()
    if ch == "f":
        subprocess.run(["open", str(final_mp4.parent)])
    elif ch == "v":
        subprocess.run(["open", str(final_mp4)])

if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print(f"\n[error] {ex}", file=sys.stderr)
        sys.exit(1)

# end of karaoke_time_by_miguel.py
