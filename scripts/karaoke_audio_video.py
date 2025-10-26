#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_audio_video.py – Demucs separation, mixing, and final video render.
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
        print("\nSelect stems (1–6, 9=vocals 25%, 0=done):")
        for idx, (label, key) in enumerate(kc.STEM_MENU, 1):
            mark = "x" if key in chosen else " "
            print(f"{idx}. [{mark}] {label}")
        print("9. [ ] vocals-only buddy preset (25%)")
        sel = input("> ").strip()
        if sel == "0":
            break
        elif sel == "9":
            chosen = {"vocals"}
            break
        elif sel in [str(i) for i in range(1, 7)]:
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
    if input(f"You selected: {', '.join(names)}. Confirm? [y/N] ").lower() != "y":
        return interactive_stem_selection(False)

    levels = {name: 100 for name in kc.STEM_ORDER}
    for label, key in kc.STEM_MENU:
        if key in chosen:
            default = "25" if key == "vocals" else "100"
            val = input(f"{label} volume % (default {default}): ").strip()
            levels[key] = int(val) if val.isdigit() else int(default)

    print("\nStem levels:")
    for label, key in kc.STEM_MENU:
        print(f"  {label:<10}{levels[key]:>4}%")
    if input("Proceed with Demucs + mix? [y/N] ").lower() != "y":
        return interactive_stem_selection(False)

    return kc.StemPlan(selected={k: levels[k] for k in chosen}, all_levels=levels)


def run_demucs_separation(audio_path: Path, model: str, device: Optional[str], out_root: Path) -> Path:
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


def mix_stems_to_file(stem_dir: Path, levels: Dict[str, int], out_mp3: Path):
    inputs, filters, segs = [], [], []
    for i, stem in enumerate(kc.STEM_ORDER):
        wav = stem_dir / f"{stem}.wav"
        if not wav.exists():
            kc.die(f"Missing stem: {wav}")
        inputs += ["-i", str(wav)]
        vol = levels.get(stem, 100)
        if vol == 100:
            segs.append(f"[{i}:a]")
        else:
            filters.append(f"[{i}:a]volume={max(vol, 0)/100.0}[a{i}]")
            segs.append(f"[a{i}]")
    fc = ";".join(filters + [f"{''.join(segs)}amix=inputs=6:normalize=0[aout]"])
    kc.info("🎚️ Mixing stems…")
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[aout]",
           "-c:a", "libmp3lame", "-q:a", "2", str(out_mp3)]
    kc.run(cmd, check=True)
    kc.info(f"🎧 Mixed → {out_mp3}")


def run_demucs_if_needed(audio_path: Path, args, out_dir: Path, plan: kc.StemPlan, any_change: bool) -> Path:
    if args.skip_demucs:
        return audio_path
    demucs_root = out_dir / "demucs_stems"
    demucs_dir = run_demucs_separation(audio_path, args.model, args.device, demucs_root)
    lvls = dict(plan.all_levels)
    if not any_change:
        lvls["vocals"] = 0
    target = out_dir / (f"{audio_path.stem}_buddy_mix.mp3" if any_change else f"{audio_path.stem}_instrumental.mp3")
    mix_stems_to_file(demucs_dir, lvls, target)
    return target


def build_filter_complex_with_emoji(
    ass_path: Path,
    bg_color: str,
    resolution: str,
    fps: int,
    emoji_specs,
    extra_png_inputs: list,
):
    """
    Build ffmpeg -filter_complex chain.
    PNG indexes are 1-based (audio is input 0).
    """
    filter_lines = [
        f"color=c={bg_color}:s={resolution}:r={fps}[bg]",
        f"[bg]ass={ass_path.as_posix()}:fontsdir=assets[v0]"
    ]
    current_label = "v0"
    next_id = 1
    for spec in emoji_specs:
        png_label = f"{spec['png_stream_index']}:v"
        out_label = f"v{next_id}"
        enable = f"between(t\\,{spec['start']:.3f}\\,{spec['end']:.3f})"
        filter_lines.append(
            f"[{current_label}][{png_label}]overlay="
            f"x={spec['x']}:y={spec['y']}:enable='{enable}'[{out_label}]"
        )
        current_label = out_label
        next_id += 1
    return ";".join(filter_lines), current_label, extra_png_inputs


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
    m = re.match(r"^(\d+)x(\d+)$", resolution)
    if not m:
        kc.die(f"Bad --resolution {resolution}")
    w, h = int(m[1]), int(m[2])

    emoji_specs, png_inputs = ke.build_emoji_overlays(
        lines=lines, starts=starts, offset=offset,
        canvas_w=w, canvas_h=h, font_px=font_px,
        out_dir=out_mp4.parent,
    )

    cmd = ["ffmpeg", "-y", "-i", str(audio_path)]
    for p in png_inputs:
        cmd += ["-i", p]

    fc, final_label, _ = build_filter_complex_with_emoji(
        ass_path, bg_color, resolution, fps, emoji_specs, png_inputs
    )

    cmd += [
        "-filter_complex", fc,
        "-map", f"[{final_label}]",
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out_mp4)
    ]

    kc.info("🎬 Rendering final MP4 with emojis…")
    kc.run(cmd, check=True)
    kc.info(f"📽 Video → {out_mp4}")
# end of karaoke_audio_video.py
