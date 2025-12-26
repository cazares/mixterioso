# Mixterioso (clean pipeline)

A minimal, single-entrypoint karaoke pipeline.

## Install

```bash
pip3 install -r requirements.txt
```

System dependencies (must be on PATH):
- `ffmpeg`
- `yt-dlp` (installed via `requirements.txt` also provides the `yt-dlp` command)
- Optional: `demucs` (only needed for stems-based mixing)

## Run

```bash
python3 scripts/main.py --query "Artist - Title"
```

### Overwrite behavior

- Default: reuse existing artifacts (safe-by-default)
- `--force`: overwrite without prompts
- `--confirm`: prompt before overwriting and enable the offset review flow
- `--dry-run`: print actions but don’t write/overwrite

### Audio mixing

Default audio mode is a straight copy of `mp3s/<slug>.mp3` to `mixes/<slug>.mp3`.

```bash
# Full mix (default)
python3 scripts/main.py --query "Artist - Title" --mix full

# Instrumental (requires Demucs stems; vocals muted)
python3 scripts/main.py --query "Artist - Title" --mix instrumental

# Stems mix with per-stem gain (dB)
python3 scripts/main.py --query "Artist - Title" --mix stems --vocals-db -3 --bass-db 0 --drums-db 0 --other-db 0
```

### Offset

If you provide `--offset`, it is applied to every timing line.

If you do not provide `--offset`, the default is:
- `1.0` seconds when timings were sourced from `.lrc`
- `0.0` seconds when timings were sourced from captions (`.vtt`)

```bash
python3 scripts/main.py --query "Artist - Title" --offset 0.5
```

## Outputs

Outputs are written next to `scripts/`:
- `txts/<slug>.txt`
- `timings/<slug>.lrc` (if available)
- `timings/<slug>.csv` (canonical)
- `mp3s/<slug>.mp3`
- `mixes/<slug>.mp3` or `mixes/<slug>.wav`
- `output/<slug>.mp4`
- `meta/<slug>.step1.json`

