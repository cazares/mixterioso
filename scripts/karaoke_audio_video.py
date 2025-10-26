#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_audio_video.py – Demucs separation, mixing, and final video render.
Additive changes:
- interactive stem selection with "9" auto vocals=25%
- color emoji overlay support via karaoke_emoji.build_emoji_overlays
- render_karaoke_video now builds one ffmpeg filter_complex that:
   background color -> ass=subs -> overlay emoji PNGs (enable gated by time)
"""

import sys
from pathlib import Path
from typing import Optional, Dict
import karaoke_core as kc
import karaoke_emoji as ke

def interactive_stem_selection(no_prompt: bool) -> kc.StemPlan:
    if no_prompt:
        levels = {name: 100 for name in kc.STEM_ORDER}
        return kc.StemPlan(selected={}, all_levels=levels)

    chosen = set()
    while True:
        print("\nSelect stems (1-6, 9=vocals only at 25%, 0=done):")
        for idx, (label, key) in enumerate(kc.STEM_MENU, 1):
            mark = "x" if key in chosen else " "
            print(f"{idx}. [{mark}] {label}")
        print("9. [ ] vocals at 25% buddy-mix preset")
        sel = input("> ").strip()

        if sel == "0":
            break
        elif sel == "9":
            chosen = {"vocals"}
            break
        elif sel in [str(i) for i in range(1,7)]:
            idx = int(sel) - 1
            key = kc.STEM_MENU[idx][1]
            if key in chosen:
                chosen.remove(key)
            else:
                chosen.add(key)
        else:
            print("Enter 0–6 or 9")

    if not chosen:
        levels = {name: 100 for name in kc.STEM_ORDER}
        return kc.StemPlan(selected={}, all_levels=levels)

    names = [label for (label, key) in kc.STEM_MENU if key in chosen]
    yn = input(f"You selected: {', '.join(names)} – confirm? [y/N]: ").strip().lower()
    if yn != "y":
        return interactive_stem_selection(no_prompt=False)

    levels = {name: 100 for name in kc.STEM_ORDER}
    for label, key in kc.STEM_MENU:
        if key in chosen:
            default = "25" if key == "vocals" else "100"
            val = input(f"{label} volume % (0–100, default={default}): ").strip()
            pct = int(val) if val.isdigit() else int(default)
            levels[key] = pct

    print("\nStem levels:")
    for label, key in kc.STEM_MENU:
        print(f"  {label:<10} {levels[key]:>6}")
    yn2 = input("Proceed with Demucs + mix? [y/N]: ").strip().lower()
    if yn2 != "y":
        return interactive_stem_selection(no_prompt=False)

    return kc.StemPlan(
        selected={k: levels[k] for k in chosen},
        all_levels=levels
    )

def run_demucs_separation(
    audio_path: Path,
    model: str,
    device: Optional[str],
    out_root: Path
) -> Path:
    kc.ensure_dir(out_root)
    demucs_dir = out_root / model / audio_path.stem
    if all((demucs_dir / f"{s}.wav").exists() for s in kc.STEM_ORDER):
        kc.info(f"♻ Reusing stems in {demucs_dir}")
        return demucs_dir

    cmd = ["demucs", "--name", model, "--out", str(out_root)]
    if device:
        cmd += ["--device", device]
    cmd.append(str(audio_path))
    kc.run(cmd, check=True)
    return demucs_dir

def mix_stems_to_file(
    stem_dir: Path,
    levels: Dict[str,int],
    out_mp3: Path
):
    inputs   = []
    filters  = []
    segs     = []
    for i, stem in enumerate(kc.STEM_ORDER):
        wav_path = stem_dir / f"{stem}.wav"
        if not wav_path.exists():
            kc.die(f"Missing stem file: {wav_path}")
        inputs += ["-i", str(wav_path)]
        vol = levels.get(stem, 100)
        if vol == 100:
            segs.append(f"[{i}:a]")
        else:
            filters.append(f"[{i}:a]volume={max(vol,0)/100.0}[a{i}]")
            segs.append(f"[a{i}]")

    fc = ""
    if filters:
        fc += ";".join(filters) + ";"
    fc += f"{''.join(segs)}amix=inputs=6:normalize=0[aout]"

    kc.info("🎚️  Mixing stems into final accompaniment...")
    cmd = [
        "ffmpeg","-y",
        *inputs,
        "-filter_complex", fc,
        "-map","[aout]",
        "-c:a","libmp3lame","-q:a","2",
        str(out_mp3)
    ]
    kc.run(cmd, check=True)
    kc.info(f"🎧 Mixed audio → {out_mp3}")

def run_demucs_if_needed(
    audio_path: Path,
    args,
    out_dir: Path,
    stem_plan: kc.StemPlan,
    any_change: bool
) -> Path:
    if args.skip_demucs:
        return audio_path

    demucs_out_root = out_dir / "demucs_stems"
    demucs_dir = run_demucs_separation(audio_path, args.model, args.device, demucs_out_root)

    lvls = dict(stem_plan.all_levels)
    if not any_change:
        # user did not change anything -> default instrumental-style
        lvls["vocals"] = 0

    target_audio = (
        out_dir / f"{audio_path.stem}_buddy_mix.mp3"
        if any_change else
        out_dir / f"{audio_path.stem}_instrumental.mp3"
    )

    mix_stems_to_file(demucs_dir, lvls, target_audio)
    return target_audio

def build_filter_complex_with_emoji(
    ass_path: Path,
    bg_color: str,
    resolution: str,
    fps: int,
    emoji_specs,
    extra_png_inputs: list,
):
    """
    Build ffmpeg -filter_complex string and output video label.
    Plan:
      [base] color src
      ass=subtitles.ass => [v0]
      overlay emoji PNGs sequentially: [v0][2:v]overlay=... -> [v1], etc.
    We assume main audio comes separately via -i audio.
    """
    filter_lines = []
    filter_lines.append(
        f"color=c={bg_color}:s={resolution}:r={fps}[bg]"
    )
    # ✅ Key fix: explicitly add fontsdir=assets
    filter_lines.append(
        f"[bg]ass={ass_path.as_posix()}:fontsdir=assets[v0]"
    )

    current_label = "v0"
    next_idx = 0
    for spec in emoji_specs:
        png_label = f"{spec['png_stream_index']}:v"
        out_label = f"v{next_idx+1}"
        enable_expr = f"between(t\\,{spec['start']:.3f}\\,{spec['end']:.3f})"
        filter_lines.append(
            f"[{current_label}][{png_label}]overlay="
            f"x={spec['x']}:y={spec['y']}:enable='{enable_expr}'[{out_label}]"
        )
        current_label = out_label
        next_idx += 1

    final_video_label = current_label
    return ";".join(filter_lines), final_video_label, extra_png_inputs

def render_karaoke_video(
    audio_path: Path,
    ass_path: Path,
    out_mp4: Path,
    resolution: str,
    fps: int,
    bg_color: str,
    lines,
    starts,
    offset,
    font_px,
):
    import re
    m = re.match(r"^(\d+)x(\d+)$", resolution.strip().lower())
    if not m:
        kc.die(f"Bad --resolution: {resolution}")
    canvas_w, canvas_h = int(m.group(1)), int(m.group(2))

    emoji_specs, png_inputs = ke.build_emoji_overlays(
        lines=lines,
        starts=starts,
        offset=offset,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        font_px=font_px,
        out_dir=out_mp4.parent,
    )

    cmd = ["ffmpeg", "-y"]
    cmd += ["-i", str(audio_path)]
    for png_path in png_inputs:
        cmd += ["-i", png_path]

    filter_complex, final_label, _ = build_filter_complex_with_emoji(
        ass_path=ass_path,
        bg_color=bg_color,
        resolution=resolution,
        fps=fps,
        emoji_specs=emoji_specs,
        extra_png_inputs=png_inputs,
    )

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-map", "0:a",
        "-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","192k",
        "-shortest",
        "-movflags","+faststart",
        str(out_mp4),
    ]

    kc.info("🎬 Rendering final MP4 (with emoji overlays if available)...")
    kc.run(cmd, check=True)
    kc.info(f"📽 Final video → {out_mp4}")

# end of karaoke_audio_video.py
