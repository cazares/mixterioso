# scripts/app_align.py
# FastAPI microservice to align lyrics to audio using the shared aligner module.
# Endpoints:
#   GET  /health
#   POST /align     { slug, mode?, pad_head?, ... }  -> writes timings/<slug>.csv and returns JSON
#   POST /align_fs  { txt_path, audio_path, out_csv, mode?, ... } -> explicit paths version
#
# One-liner:
#   uvicorn scripts.app_align:app --reload --host 0.0.0.0 --port 8010

from __future__ import annotations
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pathlib import Path
import time
import sys

# local import path
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from aligner import (  # type: ignore
    AlignConfig,
    align_txt_to_audio,
    align_txt_to_audio_smart,
)

TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"

app = FastAPI(title="Mixterioso — Align Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

class AlignBySlugReq(BaseModel):
    slug: str = Field(..., description="Slug used to locate txts/<slug>.txt and mp3s/<slug>.mp3")
    mode: str = Field("naive", regex="^(naive|smart)$")
    pad_head: float = 0.75
    pad_tail: float = 0.75
    min_step: float = 1.0
    max_step: float = 6.0
    verbose: bool = False

class AlignByFSReq(BaseModel):
    txt_path: str
    audio_path: str
    out_csv: str
    mode: str = Field("naive", regex="^(naive|smart)$")
    pad_head: float = 0.75
    pad_tail: float = 0.75
    min_step: float = 1.0
    max_step: float = 6.0
    verbose: bool = False

@app.get("/health")
def health():
    return {"ok": True, "service": "align", "ts": time.time()}

@app.post("/align")
def align_slug(req: AlignBySlugReq):
    slug = req.slug.strip()
    txt_path = TXT_DIR / f"{slug}.txt"
    audio_path = MP3_DIR / f"{slug}.mp3"
    out_csv = TIMINGS_DIR / f"{slug}.csv"

    if not txt_path.exists():
        raise HTTPException(404, f"TXT not found: {txt_path}")
    if not audio_path.exists():
        raise HTTPException(404, f"Audio not found: {audio_path}")

    cfg = AlignConfig(
        pad_head=req.pad_head, pad_tail=req.pad_tail,
        min_step=req.min_step, max_step=req.max_step,
    )

    if req.mode == "smart":
        align_txt_to_audio_smart(txt_path, audio_path, out_csv, cfg, verbose=req.verbose)
    else:
        align_txt_to_audio(txt_path, audio_path, out_csv, cfg, verbose=req.verbose)

    return {
        "slug": slug,
        "mode": req.mode,
        "timings_csv": str(out_csv),
        "counts": {"lines": sum(1 for _ in (TXT_DIR / f"{slug}.txt").read_text(encoding="utf-8", errors="ignore").splitlines())}
    }

@app.post("/align_fs")
def align_fs(req: AlignByFSReq):
    txt_path = Path(req.txt_path)
    audio_path = Path(req.audio_path)
    out_csv = Path(req.out_csv)
    if not txt_path.exists():
        raise HTTPException(404, f"TXT not found: {txt_path}")
    if not audio_path.exists():
        raise HTTPException(404, f"Audio not found: {audio_path}")
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    cfg = AlignConfig(
        pad_head=req.pad_head, pad_tail=req.pad_tail,
        min_step=req.min_step, max_step=req.max_step,
    )

    if req.mode == "smart":
        align_txt_to_audio_smart(txt_path, audio_path, out_csv, cfg, verbose=req.verbose)
    else:
        align_txt_to_audio(txt_path, audio_path, out_csv, cfg, verbose=req.verbose)

    return {"ok": True, "timings_csv": str(out_csv)}

# end of app_align.py
