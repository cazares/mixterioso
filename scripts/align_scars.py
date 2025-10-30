# scripts/align_scars.py
# deps: faster-whisper==1.0.3, whisperx==3.1.1, torch==2.4.1, torchaudio==2.4.1, numpy==1.26.4, pandas, soundfile, av==12.0.0
import unicodedata, re, pandas as pd
from faster_whisper import WhisperModel
from whisperx.alignment import load_align_model, align   # avoids pyannote/vad import

def norm(s):
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("’","'").replace("—","-")
    s = re.sub(r"[^\w\s'\-]", " ", s)
    return re.sub(r"\s+"," ", s).strip()

audio = "separated/htdemucs_6s/Scar_Tissue/vocals.wav"
lines = [l.strip() for l in open("lyrics/scar_tissue.txt").read().splitlines() if l.strip()]
norm_lines = [norm(l) for l in lines]

# 1) ASR (no VAD)
fw = WhisperModel("large-v3", device="cpu", compute_type="int8")
segments, _ = fw.transcribe(audio, language="en", task="transcribe", word_timestamps=True, vad_filter=False)

segx = [{
    "start": s.start, "end": s.end, "text": s.text,
    "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])]
} for s in segments]

# 2) Forced alignment to your lyrics
align_model, metadata = load_align_model(language_code="en", device="cpu")
aligned = align(" ".join(norm_lines), segx, align_model, metadata, audio, "cpu")
words = aligned["word_segments"];  [w.update(n=norm(w["word"])) for w in words]

# 3) Map words → line starts
out, wi, last_t = [], 0, 0.0
for i, line in enumerate(norm_lines):
    toks, t0, k = line.split(), "", 0
    while wi < len(words) and k < len(toks):
        if words[wi]["n"] == toks[k]:
            if not t0: t0 = f"{max(last_t, words[wi]['start']):.3f}"
            k += 1
        wi += 1
    if t0: last_t = float(t0)
    out.append((lines[i], t0))

pd.DataFrame(out, columns=["line","start"]).to_csv("scar_tissue_synced.csv", index=False)
# end of scripts/align_scars.py
