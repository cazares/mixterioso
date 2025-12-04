# 📀 Mixterioso Karaoke Pipeline — README (Dec-4 LKGV)

A fast, manual-first, precision-controlled karaoke creation pipeline. Designed for full human control, zero AI drift, clean visuals, and stable re-runs.

This pipeline takes a song from metadata → audio download → stem remix → lyric timing → MP4 render → YouTube upload using clean modular scripts.

---

# 🗺️ Pipeline Overview

| Step | Script            | Purpose |
|------|-------------------|---------|
| 0    | `0_master.py`     | Orchestrator: runs Steps 1–5 interactively |
| 1    | `1_txt_mp3.py`    | Fetch artist/title → lyrics → MP3 source |
| 2    | `2_stems.py`      | Demucs separation + custom mix → `mixes/<slug>.wav` |
| 3    | `3_timing.py`     | Manual timestamping (curses UI) |
| 4    | `4_mp4.py`        | Render final MP4 with karaoke visuals |
| 5    | `5_upload.py`     | Upload to YouTube with title builder + thumbnail |

Everything is powered by `mix_utils.py`, which defines paths, logging, and safety helpers.

---

# 🚀 Quick Start

### Run the full pipeline
```bash
python3 scripts/0_master.py
```

### Run steps manually
```bash
python3 scripts/1_txt_mp3.py --slug "my_song"
python3 scripts/2_stems.py --slug my_song
python3 scripts/3_timing.py --slug my_song
python3 scripts/4_mp4.py --slug my_song --offset -1.5
python3 scripts/5_upload.py --slug my_song
```

---

# 🧩 Directory Structure

```
mixterioso/
│
├── scripts/
│   ├── 0_master.py
│   ├── 1_txt_mp3.py
│   ├── 2_stems.py
│   ├── 3_timing.py
│   ├── 4_mp4.py
│   ├── 5_upload.py
│   └── mix_utils.py
│
├── mp3s/
├── txts/
├── separated/
├── mixes/
├── timings/
├── output/
└── meta/
```

---

# 🎚️ Step-by-Step Behavior

## Step 1 — `1_txt_mp3.py`
- Fetch lyrics + MP3
- Create canonical slug
- Write:
  - `txts/<slug>.txt`
  - `mp3s/<slug>.mp3`
  - `meta/<slug>.json`

## Step 2 — `2_stems.py`
- Run Demucs
- UI to remix stems
- Always writes:
```
mixes/<slug>.wav
```

## Step 3 — `3_timing.py`  
Manual curses timing UI.

Hotkeys:
- ENTER = stamp lyric
- s = skip
- p = pause/resume
- e/r/t = rewind 1/3/5 sec
- d/f/g = forward 1/3/5 sec
- 1–= = insert notes
- b = blank
- q = save + quit

Writes:
```
timings/<slug>.csv
```

## Step 4 — `4_mp4.py`
- Classic karaoke visuals restored  
- Divider + "Next:" preview  
- Title card  
- Fade transitions  
- Offset applied during render (`--offset`)  

Outputs:
```
output/<slug>.mp4
output/<slug>.ass
```

Uses only:
```
mixes/<slug>.wav
```

## Step 5 — `5_upload.py`
- OAuth login  
- Title builder (presets + custom)  
- Optional description  
- Auto thumbnail (0.5s)  

Outputs:
```
youtube_token.json
output/<slug>.jpg
```

---

# 🎨 Visual System (Step 4)

Controlled via constants in `4_mp4.py`:

- Band sizes
- Font scaling
- Colors + alpha
- Fade durations
- Divider geometry
- Offset
- Title behavior

---

# 🔧 Requirements

- Python 3.10+
- ffmpeg + ffprobe
- afplay or ffplay
- yt-dlp
- demucs
- Google API libraries

---

# ❓ Troubleshooting

### Lyrics out of sync?
```
python3 scripts/4_mp4.py --slug song --offset -1.7
```

### Wrong audio?
```
rm mixes/song.wav
python3 scripts/2_stems.py --slug song
```

### Upload error?
Ensure:
- OAuth token exists
- `YOUTUBE_CLIENT_SECRETS_JSON` is set
- Network connectivity

---

# 🧊 Checkpoint
**Dec-4 Pipeline B-Profile LKGV** — authoritative version snapshot.
