# 🎤 Karaoke Time  
*A lyric video generator by Miguel Cazares*

Karaoke Time is a **Python toolkit** that creates karaoke-style lyric videos with synchronized subtitles, customizable visuals, and optional **“Perform Along Buddy”** stem mixing powered by [Demucs](https://github.com/facebookresearch/demucs).  
It’s built for musicians, performers, and creators who want to make professional-quality karaoke or rehearsal videos straight from text files — **no DAW required**.

---

## ✨ Features

### 🎵 Audio & Timing
- **Tap-to-time lyric synchronization**
  - Manual timing loop for precise control
  - Auto-reuse of existing CSV files
- **6-stem mixing support**
  - Interactive volume control for vocals, bass, drums, piano, guitar, and other stems
  - Optional “Buddy Mode” for singing/playing along with partial vocals
- **Offset correction** (`--offset`) for fine-tuning global lyric alignment

### 🎨 Visual Output
- **Configurable subtitles**
  - Font size (`--font-size`)
  - Resolution (`--resolution`)
  - Background color (`--bg-color`)
- **High-quality render**
  - H.264 + AAC MP4 output via `ffmpeg`
  - `+faststart` flag for instant web playback
- **Colorized, emoji-rich console logs** for better progress visibility 🖥️🎶

### ⚙️ Workflow Automation
- **Automatic environment setup**
  - Run `initialize_environment.sh` to clean caches and install dependencies
- **Smart caching**
  - Skips Demucs re-separation if stems already exist
  - Reuses previously timed CSV or ASS files when available
- **Non-interactive mode**
  - `--no-prompt` runs everything automatically from start to finish
- **Dry-run simulation**
  - `--dry-run` prints all planned steps without processing

---

## 🚀 Quick Start

### 1️⃣ Initialize Environment
Run once to prepare the project and free Codespaces storage:

```bash
bash initialize_environment.sh
```

This will:
- Clean temporary files and cached models  
- Create a `demucs_env` virtual environment  
- Install all Python dependencies automatically  

Activate it afterward:

```bash
source demucs_env/bin/activate
```

---

### 2️⃣ Generate a Karaoke Video
Example end-to-end run:

```bash
python3 karaoke_time_by_miguel.py \
  --lyrics "lyrics/John_Frusciante_The_Past_Recedes.txt" \
  --audio "songs/John_Frusciante_The_Past_Recedes.mp3" \
  --font-size 140 \
  --offset -2 \
  --no-prompt
```

You’ll be prompted (unless `--no-prompt`) to select stem volumes interactively — vocals, bass, drums, etc.  
Once finished, you’ll find your video in `output/<song_name>/`.

---

### 3️⃣ Manual Lyric Timing (Optional)
If you want to retime or manually sync a lyrics file:

```bash
python3 karaoke_time_by_miguel.py \
  --lyrics "lyrics/MySong.txt" \
  --audio "songs/MySong.mp3"
```

This activates **Tap-to-Time Mode** — press **Enter** when each line should appear.  
The resulting CSV will be saved automatically for reuse.

---

## 📁 Project Structure

```
karaoke-time-by-miguel/
├── karaoke_time_by_miguel.py       # All-in-one main script
├── initialize_environment.sh       # Unified setup + cleanup
├── lyrics/                         # Plain-text lyric files
│   ├── Artist_Title.txt
│   └── Artist_Title_synced.csv
├── output/
│   └── Artist_Title/
│       ├── *_instrumental.mp3
│       ├── *_buddy_mix.mp3
│       ├── *_subtitles.ass
│       └── *_karaoke.mp4
└── separated/                      # Demucs-generated stems (cached)
```

---

## 🧩 Dependencies

Installed automatically via `initialize_environment.sh` or `requirements.txt` fallback:

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
```

---

## 💡 Tips

- 🧠 Use `--dry-run` to preview steps without processing.
- 🎚️ You can set all stems to 100% for a full mix, or reduce vocals to 0% for instrumentals.
- ⚡ Skipping Demucs reuse existing separated stems — much faster on re-runs.
- 🎬 To overwrite subtitle style or offset, just rerun with new flags; ASS files regenerate automatically.

---

## 🧑‍💻 Author
**Miguel Cazares**  
[https://miguelengineer.com](https://miguelengineer.com)  

Built with ❤️ for musicians who love code and karaoke.

---
# end of README.md
