# pip3 install faster-whisper==1.0.3 pandas rapidfuzz
import re, unicodedata, pandas as pd
from pathlib import Path
from rapidfuzz.sequence import ratio
from faster_whisper import WhisperModel

def norm(s):
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = s.replace("’","'").replace("—","-")
    s = re.sub(r"[^\w\s'\-]", " ", s)
    return re.sub(r"\s+"," ", s).strip()

audio = "separated/htdemucs_6s/Scar_Tissue/vocals.wav"   # vocals stem
lines = [l.strip() for l in open("lyrics/scar_tissue.txt").read().splitlines() if l.strip()]
norm_lines = [norm(l) for l in lines]

# ASR with word timestamps only (no VAD, no pyannote)
fw = WhisperModel("large-v3", device="cpu", compute_type="int8")
segments, _ = fw.transcribe(audio, language="en", task="transcribe",
                            word_timestamps=True, vad_filter=False)

# Flatten words
W = []
for s in segments:
    for w in (s.words or []):
        if w.start is None: 
            continue
        for tok in norm(w.word).split():
            if tok: W.append((tok, w.start))

W_tok = [t for t,_ in W]
W_tim = [t for _,t in W]

# Align each lyric line to a sliding window over words with fuzzy scoring
out, i = [], 0
for raw, ln in zip(lines, norm_lines):
    if not ln:
        out.append((raw, "")); continue
    ltoks = ln.split()
    best_t, best_s = "", -1.0
    # search within next ~500 tokens to keep monotone progression
    end = min(len(W_tok), i + 500)
    for j in range(i, end):
        win = W_tok[j:j+min(len(ltoks)+3, 12)]
        s = ratio(" ".join(ltoks[:6]), " ".join(win[:7])) / 100.0
        if s > best_s:
            best_s = s
            best_t = f"{max(float(out[-1][1] or 0.0), W_tim[j]):.3f}"
    # accept if reasonably close; else leave blank
    out.append((raw, best_t if best_s >= 0.42 else ""))
    if best_s >= 0.42:
        # advance pointer to keep time monotone
        i = max(i, j)

pd.DataFrame(out, columns=["line","start"]).to_csv("scar_tissue_synced.csv", index=False)
# end of scripts/align_fuzzy.py
