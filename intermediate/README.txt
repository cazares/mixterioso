
🎤 Karaoke Time v3.3 — by Miguel Cázares
----------------------------------------
This version automates lyric fetching, editing, and video creation.

Quick Start
===========
1. Place your song MP3 anywhere and run:
   python3 karaoke_generator.py "path/to/song.mp3"

2. Wait for lyrics to be fetched automatically.
   Edit the generated FINAL_Artist__Title.txt file to insert \N line breaks.
   Save it, then press Enter when prompted.

3. The script will handle timing, rendering, and video generation automatically.

Optional:
   python3 karaoke_generator.py "path/to/song.mp3" --strip-vocals
   (uses Demucs to create an instrumental first)

Folder Layout
=============
karaoke_time/
├── karaoke_core.py
├── karaoke_time.py
├── karaoke_generator.py
├── karaoke_maker.py
├── pause_media.applescript
└── songs/
    └── Artist__Title/
        ├── audio/
        ├── lyrics/
        │   ├── auto_Artist__Title.txt
        │   └── FINAL_Artist__Title.txt
        ├── output/
        └── logs/

Enjoy!
