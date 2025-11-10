📀 Karaoke Pipeline README

Welcome to the manual-first, ultra-optimized karaoke creation pipeline. It runs from raw video download to timestamped subtitles to a full MP4 export — human-guided and bulletproof.

🗺️ Pipeline Overview

| Step | Script            | Purpose                                    |
|------|-------------------|--------------------------------------------|
| 1    | `1_download.py`   | Download audio and lyrics (if missing)     |
| 2    | `2_mix.py`        | Display audio stem UI and optionally split |
| 3    | `3_time.py`       | Manually timestamp each lyrics line        |
| 4    | `4_calibrate.py`  | Adjust A/V sync interactively              |
| 5    | `5_gen_mp4.py`    | Generate final karaoke-style MP4           |

Everything is orchestrated by `0_master.py`, which can auto-run all steps.

🚀 Quick Start

```bash
# Run the full pipeline
python 0_master.py "Jerry Was a Race Car Driver"

# OR run steps manually:
python 1_download.py "Jerry Was a Race Car Driver"
python 2_mix.py jerry_was_a_race_car_driver
python 3_time.py jerry_was_a_race_car_driver
python 4_calibrate.py jerry_was_a_race_car_driver [start_sec] [end_sec]
python 5_gen_mp4.py jerry_was_a_race_car_driver
```

⚙️ Config + Behavior

- ⏱️ Manual timing is fully interactive via `curses` (Steps 3 + 4).
- 🧠 Offset is manually calibrated and saved as JSON.
- 🧼 No rework: files are cached and reused unless you say otherwise.
- 🎨 Console output is vivid and styled (via `rich`).
- 🎛️ Tune it all via constants at the top of each script (e.g., font size, directories).

🔥 Hotkeys Reference

`3_time.py`
- Space: log timestamp
- q: quit early

`4_calibrate.py`
- ← / →: adjust by ±0.1s
- ↓ / ↑: adjust by ±0.5s
- Space: play snippet
- s: save offset
- q: quit

🧩 File Structure

karaoke-time-by-miguel/
├── scripts/
│   ├── 0_master.py
│   ├── 1_download.py
│   ├── 2_mix.py
│   ├── 3_time.py
│   ├── 4_calibrate.py
│   └── 5_gen_mp4.py
├── mp3s/
├── txts/
├── timing/
├── offsets/
├── stems/
├── meta/
├── mp4s/

❓ Common Issues

- Already downloaded? Files are skipped unless missing.
- Wrong slug? Check filename slugs match across `mp3s/`, `txts/`, etc.
- Audio won’t play? Ensure `afplay` (macOS) or swap to `ffplay`.