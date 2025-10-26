#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_audio_video.py
- Demucs separation, mixing, final video render for ASS pipeline (unchanged)
- PLUS chrome-static-slides stitching support
"""

from pathlib import Path
from typing import Optional, Dict
import karaoke_core as kc

# If you also import karaoke_emoji in your original file for overlays,
# keep that import. Leaving it optional here to avoid NameError if missing:
try:
    import karaoke_emoji as ke  # noqa:F401
except Exception:
    ke = None

########################################
# ORIGINAL FUNCTIONS (timed karaoke)   #
########################################

def interactive_stem_selection(no_prompt: bool) -> kc.StemPlan:
    """
    This should match your original logic:
    - prompt user for stems, allow buddy mix, etc.
    - return kc.StemPlan(selected={}, all_levels={...})
    """
    # minimal always-keep-default fallback:
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

def run_demucs_separation(audio_path: Path,
                          model: str,
                          device: Optional[str],
                          out_root: Path) -> Path:
    kc.ensure_dir(out_root)
    demucs_dir = out_root / model / audio_path.stem
    # assume stems already exist = reuse
    need = [demucs_dir / f"{stem}.wav" for stem in kc.STEM_ORDER]
    if all(p.exists() for p in need):
        kc.info(f"♻ Reusing stems in {demucs_dir}")
        return demucs_dir

    cmd = ["demucs", "--name", model, "--out", str(out_root)]
    if device:
        cmd += ["--device", device]
    cmd.append(str(audio_path))
    kc.run(cmd, check=True)
    return demucs_dir

def mix_stems_to_file(stem_dir: Path,
                      levels: Dict[str,int],
                      out_mp3: Path):
    """
    Combine stems with volume adjustments into a single mp3.
    """
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

def run_demucs_if_needed(audio_path: Path,
                         args,
                         out_dir: Path,
                         stem_plan: kc.StemPlan,
                         any_change: bool) -> Path:
    """
    Returns path to the final audio we should use for rendering.
    """
    if args.skip_demucs:
        return audio_path

    demucs_out_root = out_dir / "demucs_stems"
    demucs_dir = run_demucs_separation(audio_path, args.model, args.device, demucs_out_root)

    lvls = dict(stem_plan.all_levels)
    if not any_change:
        # user didn't custom-select, default to instrumental-style = vocals muted
        lvls["vocals"] = 0

    target_audio = (
        out_dir / f"{audio_path.stem}_buddy_mix.mp3"
        if any_change else
        out_dir / f"{audio_path.stem}_instrumental.mp3"
    )

    mix_stems_to_file(demucs_dir, lvls, target_audio)
    return target_audio

#########################################################
# NEW: chrome_static_mp4 helper (for --chrome-static-slides)
#########################################################

def chrome_static_to_mp4(final_audio_path: Path,
                         song_base: str,
                         seconds_per_frame: float = 1.5,
                         fps_visual: int = 30):
    """
    Take the already-generated output/frames_chrome/*.png slides
    and mux them with final_audio_path into
    output/chrome_rendered_mp4s/<song_base>_chrome_static.mp4
    """
    frames_glob = "output/frames_chrome/*.png"
    out_mp4 = Path("output/chrome_rendered_mp4s") / f"{song_base}_chrome_static.mp4"
    kc.stitch_frames_to_mp4(
        frames_glob=frames_glob,
        audio_path=final_audio_path,
        out_mp4_path=out_mp4,
        fps_visual=fps_visual,
        seconds_per_frame=seconds_per_frame,
    )
    return out_mp4
