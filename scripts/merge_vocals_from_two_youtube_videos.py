#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_vocals_from_two_youtube_videos.py
Combines karaoke/instrumental video (URL A) with vocals from another video (URL B).
Keeps visuals from URL A and adds 100% vocals extracted from URL B.
"""

import os, subprocess, sys, shlex
from pathlib import Path

def run(cmd):
    print(f"\n▶️ {cmd}")
    subprocess.run(shlex.split(cmd), check=True)

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 merge_vocals_from_two_youtube_videos.py <instrumental_url> <vocal_url>")
        sys.exit(1)

    instrumental_url, vocal_url = sys.argv[1], sys.argv[2]
    base = Path("merged_output")
    base.mkdir(exist_ok=True)

    inst_mp4 = base / "instrumental.mp4"
    vocal_mp3 = base / "vocals_source.mp3"
    vocals_only = base / "vocals.wav"
    inst_audio = base / "instrumental_audio.wav"
    merged_audio = base / "merged_audio.wav"
    final_mp4 = base / "final_with_vocals.mp4"

    # Step 1: Download instrumental (with video)
    run(f'yt-dlp -f bestvideo+bestaudio -o "{inst_mp4}" "{instrumental_url}"')

    # Step 2: Download vocal source (audio only)
    run(f'yt-dlp -x --audio-format mp3 -o "{vocal_mp3}" "{vocal_url}"')

    # Step 3: Separate stems to get vocals only
    run(f'demucs -n htdemucs_ft "{vocal_mp3}"')

    stem_dir = Path("separated/htdemucs_ft") / vocal_mp3.stem
    vocals_stem = stem_dir / "vocals.wav"
    if not vocals_stem.exists():
        print("❌ Could not find vocals.wav from Demucs output.")
        sys.exit(1)

    # Step 4: Extract instrumental audio from the base video
    run(f'ffmpeg -y -i "{inst_mp4}" -vn -acodec pcm_s16le -ar 44100 -ac 2 "{inst_audio}"')

    # Step 5: Mix instrumental audio + vocals
    run(
        f'ffmpeg -y -i "{inst_audio}" -i "{vocals_stem}" '
        f'-filter_complex "[0:a][1:a]amix=inputs=2:normalize=0[out]" '
        f'-map "[out]" -c:a pcm_s16le "{merged_audio}"'
    )

    # Step 6: Combine video (from instrumental) + merged audio
    run(
        f'ffmpeg -y -i "{inst_mp4}" -i "{merged_audio}" '
        f'-map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k "{final_mp4}"'
    )

    print(f"\n✅ Done! Final video: {final_mp4.resolve()}")

if __name__ == "__main__":
    main()
