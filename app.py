#!/usr/bin/env python3
# app.py — Mixterioso API (Step 1 end-to-end pipeline, NO 3_timing.py)
# - /mp3:     YouTube URL → MP3
# - /search:  URL/ID/Query → MP3 + lyrics (delegates to 1_txt_mp3.py for queries)
# - /pipeline: Input → mp3 + txt + timings (lyrics_align_single.py) + mp4 + optional upload

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

# ---------- Paths ----------
BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"
SEPARATED_DIR = BASE_DIR / "separated"

for d in (TXT_DIR, MP3_DIR, MIXES_DIR, TIMINGS_DIR, OUTPUT_DIR, META_DIR):
    d.mkdir(parents=True, exist_ok=True)

PYTHON_BIN = sys.executable

# ---------- App ----------
app = FastAPI(title="Mixterioso — Step-1 Pipeline API", version="0.2.0")

# CORS: wide-open for dev; tighten later if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ---------- Helpers ----------
def slugify(text: str) -> str:
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"

def is_youtube_url_or_id(s: str) -> bool:
    s = s.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return True
    return bool(re.search(r"(youtube\.com|youtu\.be)", s, re.I))

def extract_video_id(s: str) -> Optional[str]:
    s = s.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else None

@dataclass
class RunResult:
    code: int
    out: str
    err: str
    secs: float

async def run_cmd(cmd: list[str], logs: list[str], tag: str, check: bool = False) -> RunResult:
    t0 = time.perf_counter()
    logs.append(f"[{tag}] $ {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(BASE_DIR)
    )
    out_b, err_b = await proc.communicate()
    t1 = time.perf_counter()
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    logs.append(f"[{tag}] exit={proc.returncode} in {t1 - t0:.2f}s")
    if out.strip():
        logs.append(f"[{tag}][stdout]\n{out.strip()}")
    if err.strip():
        logs.append(f"[{tag}][stderr]\n{err.strip()}")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{tag} failed with exit {proc.returncode}")
    return RunResult(proc.returncode, out, err, t1 - t0)

def write_pipeline_log(slug: str, logs: list[str]) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"{slug}_{ts}_pipeline.log"
    path.write_text("\n".join(logs), encoding="utf-8")
    return str(path)

def meta_artist_title(slug: str) -> tuple[Optional[str], Optional[str]]:
    meta = META_DIR / f"{slug}.json"
    if not meta.exists():
        return None, None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        return data.get("artist"), data.get("title")
    except Exception:
        return None, None

# ---------- Schemas ----------
class MP3Req(BaseModel):
    youtube_url: str
    bitrate_kbps: int = Field(default=192, ge=64, le=320)

class SearchReq(BaseModel):
    input: str
    bitrate_kbps: int = Field(default=192, ge=64, le=320)

class PipelineReq(BaseModel):
    input: str
    bitrate_kbps: int = Field(default=192, ge=64, le=320)
    profile: str = Field(default="lyrics")  # other profiles supported by your 2_stems/4_mp4
    offset: float = 0.0
    upload: bool = False
    privacy: Optional[str] = Field(default=None)           # "public"|"unlisted"|"private"
    made_for_kids: bool = False
    thumb_from_sec: Optional[float] = None
    tags_csv: Optional[str] = None
    title_override: Optional[str] = None

# ---------- Routes ----------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "cwd": str(BASE_DIR)}

@app.get("/files/{filename}")
async def serve_file(filename: str):
    # Serve from known roots
    for root in (MP3_DIR, TXT_DIR, TIMINGS_DIR, OUTPUT_DIR, MIXES_DIR, META_DIR):
        p = root / filename
        if p.exists() and p.is_file():
            return FileResponse(str(p))
    raise HTTPException(404, "file not found")

@app.head("/files/{filename}")
async def head_file(filename: str):
    for root in (MP3_DIR, TXT_DIR, TIMINGS_DIR, OUTPUT_DIR, MIXES_DIR, META_DIR):
        p = root / filename
        if p.exists() and p.is_file():
            return PlainTextResponse("", status_code=200)
    raise HTTPException(404, "file not found")

@app.post("/mp3")
async def mp3(req: MP3Req):
    logs: list[str] = []
    vid = extract_video_id(req.youtube_url) or "audio"
    out_mp3 = MP3_DIR / f"{vid}.mp3"

    # yt-dlp direct
    ytdlp_cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3",
        "--audio-quality", f"{req.bitrate_kbps}k",
        "-o", str(out_mp3), req.youtube_url
    ]
    rr = await run_cmd(ytdlp_cmd, logs, "STEP1-MP3", check=True)

    # Title extraction from stdout (best-effort)
    title = None
    m = re.search(r"\[download\] Destination: .*?/(.+?)\.mp3", rr.out)
    if m:
        title = m.group(1).replace("_", " ")
    return {
        "video_id": vid,
        "title": title or vid,
        "bitrate_kbps": req.bitrate_kbps,
        "mp3_path": str(out_mp3),
        "download_url": f"/files/{out_mp3.name}",
        "logs": logs[-50:],  # tail for brevity
    }

@app.post("/search")
async def search(req: SearchReq):
    """
    Unified: if input is URL/ID → download MP3 directly
             else (query) → delegate to scripts/1_txt_mp3.py, which writes mp3+txt+meta
    """
    logs: list[str] = []
    value = req.input.strip()
    base = value

    if is_youtube_url_or_id(value):
        # Same as /mp3 path
        vid = extract_video_id(value) or "audio"
        out_mp3 = MP3_DIR / f"{vid}.mp3"
        ytdlp_cmd = [
            "yt-dlp", "-x", "--audio-format", "mp3",
            "--audio-quality", f"{req.bitrate_kbps}k",
            "-o", str(out_mp3), value
        ]
        await run_cmd(ytdlp_cmd, logs, "STEP1-MP3", check=True)
        title_guess = vid
        return {
            "slug": slugify(title_guess),
            "title": title_guess,
            "download_url": f"/files/{out_mp3.name}",
            "lyrics_text": "",
            "search_metadata": {"mode": "url_or_id", "input": value},
            "logs": logs[-100:]
        }

    # Query path — use 1_txt_mp3.py to resolve (Genius→Musixmatch/YouTube) and write files
    if not (SCRIPTS_DIR / "1_txt_mp3.py").exists():
        raise HTTPException(500, "scripts/1_txt_mp3.py not found for query search.")
    rr = await run_cmd(
        [PYTHON_BIN, str(SCRIPTS_DIR / "1_txt_mp3.py"), value], logs, "STEP1-QUERY", check=True
    )

    # Best-effort: infer latest mp3 + txt
    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    txts = sorted(TXT_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise HTTPException(500, "MP3 not created by 1_txt_mp3.py")
    latest_mp3 = mp3s[-1]
    slug = slugify(latest_mp3.stem)

    lyrics_text = ""
    if txts:
        latest_txt = txts[-1]
        if latest_txt.stem == latest_mp3.stem:
            lyrics_text = latest_txt.read_text(encoding="utf-8", errors="ignore")

    artist, title = meta_artist_title(slug)
    full_title = f"{artist} - {title}" if artist and title else slug

    return {
        "slug": slug,
        "title": full_title,
        "download_url": f"/files/{latest_mp3.name}",
        "lyrics_text": lyrics_text,
        "search_metadata": {"mode": "query_via_1_txt_mp3", "input": value},
        "logs": logs[-200:],
    }

@app.post("/pipeline")
async def pipeline(req: PipelineReq):
    """
    One-shot: input → mp3 + txt + timings (lyrics_align_single.py) + mp4 + optional YouTube upload.
    NO USE of scripts/3_timing.py anywhere.
    """
    logs: list[str] = []
    try:
        # ---- STEP 1: MP3 + TXT (delegate behavior similar to /search) ----
        if is_youtube_url_or_id(req.input):
            vid = extract_video_id(req.input) or "audio"
            out_mp3 = MP3_DIR / f"{vid}.mp3"
            ytdlp_cmd = [
                "yt-dlp", "-x", "--audio-format", "mp3",
                "--audio-quality", f"{req.bitrate_kbps}k",
                "-o", str(out_mp3), req.input
            ]
            await run_cmd(ytdlp_cmd, logs, "STEP1-MP3", check=True)
            slug = slugify(out_mp3.stem)
            txt_path = TXT_DIR / f"{slug}.txt"
            if not txt_path.exists():
                # create empty lyrics if none resolved
                txt_path.write_text("", encoding="utf-8")
        else:
            if not (SCRIPTS_DIR / "1_txt_mp3.py").exists():
                raise HTTPException(500, "scripts/1_txt_mp3.py not found for query search.")
            await run_cmd(
                [PYTHON_BIN, str(SCRIPTS_DIR / "1_txt_mp3.py"), req.input],
                logs, "STEP1-QUERY", check=True
            )
            # infer latest products
            mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            if not mp3s:
                raise HTTPException(500, "MP3 not created by 1_txt_mp3.py")
            out_mp3 = mp3s[-1]
            slug = slugify(out_mp3.stem)
            txt_path = TXT_DIR / f"{slug}.txt"
            if not txt_path.exists():
                txt_path.write_text("", encoding="utf-8")

        mp3_path = MP3_DIR / f"{slug}.mp3"
        if not mp3_path.exists():
            # If yt-dlp wrote with video_id, ensure names align
            mp3_candidates = sorted(MP3_DIR.glob(f"{slug}*.mp3"), key=lambda p: p.stat().st_mtime)
            if mp3_candidates:
                mp3_path = mp3_candidates[-1]
            else:
                raise HTTPException(500, "MP3 file not found after Step 1.")

        # ---- STEP 2: Stems/Mix (skip for 'lyrics' profile) ----
        if req.profile != "lyrics":
            # Try 6-stem model first, then 4-stem; never 2-stem here automatically
            actual_model = None
            for model in ("htdemucs_6s", "htdemucs"):
                # Reuse if already separated
                if (SEPARATED_DIR / model / slug).exists():
                    actual_model = model
                    logs.append(f"[STEP2] Reusing stems for model {model}")
                    break
            if actual_model is None:
                # Run demucs
                for model in ("htdemucs_6s", "htdemucs"):
                    try:
                        await run_cmd(["demucs", "-n", model, str(mp3_path)], logs, "STEP2-DEMUX", check=True)
                        actual_model = model
                        break
                    except Exception as e:
                        logs.append(f"[STEP2] model {model} failed; trying next… ({e})")
                if actual_model is None:
                    raise HTTPException(500, "Demucs failed for 6-stem and 4-stem models.")

            # Mix UI/render using your 2_stems.py (UI-less render)
            if (SCRIPTS_DIR / "2_stems.py").exists():
                # render-only
                out_wav = MIXES_DIR / f"{slug}_{req.profile}.wav"
                await run_cmd(
                    [PYTHON_BIN, str(SCRIPTS_DIR / "2_stems.py"),
                     "--mp3", str(mp3_path), "--profile", req.profile,
                     "--model", actual_model, "--render-only", "--output", str(out_wav)],
                    logs, "STEP2-RENDER", check=True
                )
            else:
                logs.append("[STEP2] scripts/2_stems.py missing; skipping mix render.")

        # ---- STEP 3: Timings via lyrics_align_single.py ONLY ----
        timings_csv = TIMINGS_DIR / f"{slug}.csv"
        timings_csv.parent.mkdir(parents=True, exist_ok=True)

        if (BASE_DIR / "lyrics_align_single.py").exists():
            await run_cmd(
                [PYTHON_BIN, str(BASE_DIR / "lyrics_align_single.py"),
                 "--txt", str(TXT_DIR / f"{slug}.txt"),
                 "--audio", str(mp3_path),
                 "--timings", str(timings_csv)],
                logs, "STEP3-ALIGN", check=True
            )
        else:
            # Fallback: naive timings so we can still render during demos
            logs.append("[STEP3-FALLBACK] lyrics_align_single.py not found; writing naive timings.")
            lines = [
                ln.strip() for ln in (TXT_DIR / f"{slug}.txt").read_text(encoding="utf-8", errors="ignore").splitlines()
                if ln.strip()
            ]
            if not lines:
                lines = ["…"]  # minimal placeholder
            LINE_SECS = float(os.environ.get("NAIVE_LINE_SECS", "2.5"))
            t = 0.0
            rows = ["line,start"]
            for ln in lines:
                rows.append(f"{ln.replace(',', '‚')},{t:.3f}")
                t += LINE_SECS
            timings_csv.write_text("\n".join(rows), encoding="utf-8")

        if not timings_csv.exists() or timings_csv.stat().st_size == 0:
            raise HTTPException(500, "Timings CSV not created.")

        # ---- STEP 4: MP4 render ----
        mp4_path = OUTPUT_DIR / f"{slug}_{req.profile}_offset_{('p' if req.offset>=0 else 'm')}{str(abs(req.offset)).replace('.', 'p')}s.mp4"
        if (SCRIPTS_DIR / "4_mp4.py").exists():
            await run_cmd(
                [PYTHON_BIN, str(SCRIPTS_DIR / "4_mp4.py"),
                 "--slug", slug, "--profile", req.profile, "--offset", str(req.offset)],
                logs, "STEP4-MP4", check=True
            )
        else:
            raise HTTPException(500, "scripts/4_mp4.py not found for render.")

        # normalize mp4 filename if 4_mp4.py wrote a different exact name
        latest_mp4 = sorted(OUTPUT_DIR.glob(f"{slug}_{req.profile}*.mp4"), key=lambda p: p.stat().st_mtime)
        if latest_mp4:
            mp4_path = latest_mp4[-1]

        # ---- STEP 5: Upload (optional) ----
        upload_receipt = None
        if req.upload:
            if not (SCRIPTS_DIR / "5_upload.py").exists():
                raise HTTPException(500, "scripts/5_upload.py not found for upload.")
            cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "5_upload.py"), "--file", str(mp4_path)]
            if req.title_override:
                cmd += ["--title", req.title_override]
            if req.privacy:
                cmd += ["--privacy", req.privacy]
            if req.tags_csv:
                cmd += ["--tags", req.tags_csv]
            if req.made_for_kids:
                cmd += ["--made-for-kids"]
            if req.thumb_from_sec is not None:
                cmd += ["--thumb-from-sec", str(req.thumb_from_sec)]

            rr = await run_cmd(cmd, logs, "STEP5-UPLOAD", check=False)
            # Try parsing JSON from stdout (your 5_upload.py prints JSON)
            try:
                upload_receipt = json.loads(rr.out) if rr.out.strip() else None
            except json.JSONDecodeError:
                upload_receipt = {"stdout": rr.out, "stderr": rr.err, "code": rr.code}

        # ---- Respond ----
        artist, title = meta_artist_title(slug)
        full_title = req.title_override or (f"{artist} - {title}" if artist and title else slug)

        log_path = write_pipeline_log(slug, logs)

        return {
            "ok": True,
            "slug": slug,
            "title": full_title,
            "profile": req.profile,
            "offset": req.offset,
            "artifacts": {
                "mp3": f"/files/{MP3_DIR.joinpath(f'{slug}.mp3').name}" if (MP3_DIR / f"{slug}.mp3").exists() else None,
                "txt": f"/files/{TXT_DIR.joinpath(f'{slug}.txt').name}" if (TXT_DIR / f"{slug}.txt").exists() else None,
                "timings_csv": f"/files/{timings_csv.name}",
                "mp4": f"/files/{mp4_path.name}",
                "meta": f"/files/{META_DIR.joinpath(f'{slug}.json').name}" if (META_DIR / f"{slug}.json").exists() else None,
                "pipeline_log": f"/files/{Path(log_path).name}",
            },
            "upload": upload_receipt,
            "logs_tail": logs[-200:],  # handy for the client
        }

    except HTTPException:
        raise
    except Exception as e:
        # Best-effort log dump
        slug = slugify(req.input)[:60]
        log_path = write_pipeline_log(slug, logs + [f"[ERROR] {e!r}"])
        raise HTTPException(500, f"Pipeline error: {e}. See {Path(log_path).name}")

# end of app.py
