# pip3 install faster-whisper whisperx pandas
import whisperx, pandas as pd, unicodedata, re

def norm(s):
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("’","'").replace("—","-")
    s = re.sub(r"[^\w\s'\-]", " ", s)
    return re.sub(r"\s+"," ", s).strip()

audio = "separated/htdemucs_6s/Scar_Tissue/vocals.wav"   # use Demucs vocals
lines = [l.strip() for l in open("lyrics/scar_tissue.txt").read().splitlines() if l.strip()]
norm_lines = [norm(l) for l in lines]

device = "cpu"
model = whisperx.load_model("large-v3", device)
asr = model.transcribe(audio, language="en", task="transcribe", temperature=0.0, condition_on_previous_text=False)

align_model, metadata = whisperx.load_align_model(language_code="en", device=device)
aligned = whisperx.align(" ".join(norm_lines), asr["segments"], align_model, metadata, audio, device)

words = aligned["word_segments"]  # each has word/start/end
# normalize words for matching
for w in words:
    w["n"] = norm(w["word"])

# map each lyric line to the start time of its first matched word (monotone)
out, wi, last_t = [], 0, 0.0
for i, line in enumerate(norm_lines):
    toks = line.split()
    t0, k = "", 0
    while wi < len(words) and k < len(toks):
        if words[wi]["n"] == toks[k]:
            if t0 == "":
                t0 = f"{max(last_t, words[wi]['start']):.3f}"
            k += 1
        wi += 1
    if t0:
        last_t = float(t0)
    out.append((lines[i], t0))

pd.DataFrame(out, columns=["line","start"]).to_csv("scar_tissue_synced.csv", index=False)
