# 🎤 Karaoke Time — CLI Pipeline

An end-to-end, manual-first yet automation-ready pipeline for creating professional karaoke videos.  
From YouTube audio to fully timed MP4 with synced lyrics — all modular, cache-aware, and human-verified.

---

## 🗺️ Pipeline Overview

| Step | Script | Purpose |
|------|---------|----------|
| 0 | `0_master.py` | Orchestrates the entire pipeline; can auto-run all steps. |
| 1 | `1_txt_mp3.py` | Fetches or downloads lyrics and audio (usually from YouTube). |
| 2 | `2_stems.py` | Splits audio into stems (vocals, drums, bass, etc.) via Demucs. |
| 3 | `3_timing.py` | Interactive lyric timing UI (using curses). |
| 4 | `4_mp4.py` | Renders synchronized karaoke video with styled overlays. |
| 5 | `5_upload.py` | Uploads the finished MP4 to YouTube (optional). |

---

## 🚀 Quick Start

```bash
# Clone + prepare environment
python3 -m venv demucs_env
source demucs_env/bin/activate
pip3 install -U pip wheel
pip3 install -r requirements.txt

# Run the full pipeline
python3 scripts/0_master.py --slug "imagine_-_remastered_2010" --offset -1.5

# Or run step-by-step:
python3 scripts/1_txt_mp3.py --url "https://youtu.be/YQHsXMglC9A"
python3 scripts/2_stems.py --slug adele_hello
python3 scripts/3_timing.py --slug adele_hello
python3 scripts/4_mp4.py --slug adele_hello
python3 scripts/5_upload.py --file output/adele_hello_karaoke.mp4
```

---

## ⚙️ Configuration

All scripts share environment variables loaded from `.env`:

```bash
YOUTUBE_API_KEY=your_api_key_here
OPENAI_API_KEY=your_api_key_here
GENIUS_API_KEY=your_api_key_here
```

- Output and cache directories: `mp3s/`, `stems/`, `offsets/`, `timings/`, `output/`
- Fonts and video defaults: adjustable at top of `4_mp4.py`
- ffmpeg, yt-dlp, and Demucs are required dependencies.

---

## 🧩 File Structure

```
karaoke-time-by-miguel/
├── scripts/
│   ├── 0_master.py
│   ├── 1_txt_mp3.py
│   ├── 2_stems.py
│   ├── 3_timing.py
│   ├── 4_mp4.py
│   └── 5_upload.py
├── mp3s/
├── stems/
├── timings/
├── offsets/
├── output/
└── .env
```

---

## 🧠 Core Features

- **Interactive lyric timing:** Full curses-based UI for precise alignment.  
- **Stem separation:** Powered by Demucs (`torch` + `torchaudio` backend).  
- **Smart caching:** Skips reprocessing if artifacts exist.  
- **Lyric syncing:** Auto-merges lyric text + timestamps into CSV/JSON.  
- **Video rendering:** Produces crisp MP4s using `ffmpeg-python` with ASS overlays.  
- **Auto-upload:** (Optional) Uses YouTube Data API with customizable metadata.

---

## 🎹 Hotkeys (Timing UI)

`3_timing.py`
- **Space:** Mark lyric start  
- **← / →:** Adjust ±0.1s  
- **↓ / ↑:** Adjust ±0.5s  
- **q:** Quit and save  

`4_mp4.py`
- Automatically generates up-next lyric previews and visual fades.  

---

## 🧩 Requirements

```
soundfile
demucs
torch
torchaudio
ffmpeg-python
tqdm
requests
python-dotenv
openai
yt-dlp
rich
torchcodec
```

---

## 💡 Tips

- Always re-activate your virtualenv (`source demucs_env/bin/activate`) before running scripts.  
- Use `--offset` in `0_master.py` or `4_mp4.py` to fine-tune sync (e.g., `--offset -1.75`).  
- Regenerate missing files automatically with `SAFE_REGEN=True`.  
- Use the same slug consistently across steps (e.g., `adele_hello`).  

---

## ☁️ REST API + Mobile Integration

This CLI pipeline is designed to work with a future **REST API backend** hosted on macincloud or a cloud Mac VM.

### Architecture
- **Backend (FastAPI)**: wraps each CLI script as an async endpoint.  
- **Queue/worker model**: ensures long-running jobs (e.g., Demucs separation) don’t block requests.  
- **Storage**: results stored in `output/` and accessible via signed URLs.  
- **Auth**: secured via API key or OAuth2 bearer tokens.

### Example Endpoints
| Endpoint | Method | Purpose |
|-----------|--------|----------|
| `/jobs/start` | POST | Starts pipeline job with YouTube URL |
| `/jobs/status/{id}` | GET | Polls job progress |
| `/files/{slug}/preview` | GET | Returns generated MP4 or thumbnail |

### Mobile App Integration
- The **mobile client** (React Native or Swift) uploads cookies, YouTube links, or timing files via REST.  
- Upload progress and YouTube publish status are displayed in real-time.  
- The backend can offload heavy lifting (e.g., `yt-dlp`, `demucs`, `ffmpeg`) to macincloud.

---

## 🧱 Future Expansion

- JSON-timing format with explicit `start` and `end` for every lyric.  
- Multi-user pipeline support via FastAPI background tasks.  
- Live waveform preview during timing UI.  
- Web dashboard for monitoring pipelines.  

---
