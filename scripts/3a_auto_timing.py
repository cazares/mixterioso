#!/usr/bin/env python3
# scripts/3a_auto_timing.py
# Auto-timing using Whisper + R1 alignment

from __future__ import annotations
import argparse, csv, sys
from pathlib import Path
from aligner_r1 import align_r1, Word

BASE = Path(__file__).resolve().parent.parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
TIMINGS_DIR = BASE / "timings"
TIMINGS_DIR.mkdir(exist_ok=True)

def load_txt(path: Path):
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

def whisper_words(audio_path: Path, model_size="small", lang=None, device="auto"):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device=device, compute_type="auto")
    segs, _ = model.transcribe(str(audio_path), language=lang, word_timestamps=True)
    words=[]
    for s in segs:
        if not getattr(s,"words",None): continue
        for w in s.words:
            if w.start is not None and w.end is not None and w.word:
                words.append(Word(text=w.word, start=float(w.start), end=float(w.end)))
    return words

def write_csv(slug:str, triples, out_path:Path):
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index","start_secs","end_secs","text"])
        for li, st, en, tx in triples:
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", tx])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--txt")
    ap.add_argument("--mp3")
    ap.add_argument("--model-size", default="small")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    slug = args.slug
    txt_path = Path(args.txt) if args.txt else TXT_DIR/f"{slug}.txt"
    mp3_path = Path(args.mp3) if args.mp3 else MP3_DIR/f"{slug}.mp3"

    if not txt_path.exists(): sys.exit(f"TXT not found: {txt_path}")
    if not mp3_path.exists(): sys.exit(f"Audio not found: {mp3_path}")

    lines = load_txt(txt_path)
    print(f"[3a] Loaded {len(lines)} lyric lines")

    words = whisper_words(mp3_path, model_size=args.model_size,
                          lang=args.lang, device=args.device)
    print(f"[3a] Whisper produced {len(words)} words")

    triples = align_r1(lines, words)  # R1 is the new standard
    out = TIMINGS_DIR/f"{slug}.csv"
    write_csv(slug, triples, out)

    print(f"[3a] Wrote {out} ({len(triples)} rows)")

if __name__=="__main__":
    main()
# end of 3a_auto_timing.py
