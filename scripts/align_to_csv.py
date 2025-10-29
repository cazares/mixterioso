# align_to_csv.py
import argparse, re, csv, stable_whisper

def norm(s): return re.findall(r"[a-z0-9']+", s.lower())

def assign_lines(words, lines):
    out, wi = [], 0
    for line in lines:
        toks = norm(line)
        start = end = None
        mi = 0
        while wi < len(words) and mi < len(toks):
            w = words[wi]
            wtok = norm(w['word'])
            wi += 1
            if not wtok: 
                continue
            if wtok[0] == toks[mi]:
                start = w['start'] if start is None else start
                end = w['end']
                mi += 1
        if start is None:  # fallback: pin to previous end
            start = out[-1][2] if out else 0.0
            end = start
        out.append([line, round(start,3), round(end,3)])
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="medium")  # use large-v3 if you want
    args = ap.parse_args()

    with open(args.lyrics, "r") as f:
        lines = [l.rstrip() for l in f if l.strip()]

    model = stable_whisper.load_model(args.model)  # uses MPS on Apple Silicon if available
    res = model.align(args.audio, "\n".join(lines), language="en")
    words = []
    for seg in res.to_dict()["segments"]:
        for w in seg.get("words", []):
            if w.get("word"): words.append({"word": w["word"], "start": w["start"], "end": w["end"]})
    rows = assign_lines(words, lines)

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line","start","end"])  # change if you prefer only start
        w.writerows(rows)

if __name__ == "__main__":
    main()
# end of align_to_csv.py
