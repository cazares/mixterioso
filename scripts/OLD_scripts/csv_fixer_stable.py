#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, csv, re
import stable_whisper  # pip install stable-ts

def norm_tokens(s: str):
    return re.findall(r"[a-z0-9áéíóúüñ']+", s.lower())

def extract_words_from_result(res):
    words = []
    for seg in res.to_dict().get("segments", []):
        for w in seg.get("words", []):
            if w.get("word") and isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)):
                words.append({
                    "word": w["word"],
                    "start": float(w["start"]),
                    "end": float(w["end"])
                })
    return words

def assign_lines_robust(words, lines, start_wi=0, search_ahead=400, skip_max=6, min_cover=0.6):
    W = [(norm_tokens(w["word"]) or [""])[0] for w in words]
    out = []
    wi = max(0, start_wi)
    fallback_count = 0
    for line in lines:
        tokens = norm_tokens(line)
        if not tokens:
            prev_end = out[-1][2] if out else 0.0
            out.append([line, round(prev_end, 3), round(prev_end, 3)])
            continue
        best_match = None
        end_window = min(len(W), wi + search_ahead)
        for k in range(wi, end_window):
            if W[k] != tokens[0]:
                continue
            m = 1
            j = k + 1
            last_match_idx = k
            while m < len(tokens) and j < len(W):
                hopped = 0
                while j < len(W) and W[j] != tokens[m] and hopped < skip_max:
                    j += 1; hopped += 1
                if j < len(W) and W[j] == tokens[m]:
                    last_match_idx = j
                    m += 1
                    j += 1
                else:
                    break
            score = m / len(tokens)
            if best_match is None or score > best_match[0]:
                best_match = (score, k, last_match_idx)
                if score >= 0.98:
                    break
        if best_match and best_match[0] >= min_cover:
            _, start_idx, end_idx = best_match
            start_time = words[start_idx]["start"]
            end_time   = words[end_idx]["end"]
            out.append([line, round(start_time, 3), round(end_time, 3)])
            wi = min(end_idx + 1, start_idx + search_ahead)
        else:
            prev_end = out[-1][2] if out else 0.0
            out.append([line, round(prev_end, 3), round(prev_end, 3)])
            fallback_count += 1
    return out, fallback_count

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default="es")
    ap.add_argument("--min-cover", type=float, default=0.6)
    ap.add_argument("--search-ahead", type=int, default=400)
    ap.add_argument("--skip-max", type=int, default=6)
    args = ap.parse_args()

    with open(args.lyrics, "r", encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]

    model = stable_whisper.load_model(args.model)
    result = model.align(args.audio, "\n".join(lines), language=args.language)
    words = extract_words_from_result(result)

    rows, fb_count = assign_lines_robust(
        words,
        lines,
        start_wi=0,
        search_ahead=args.search_ahead,
        skip_max=args.skip_max,
        min_cover=args.min_cover,
    )

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line", "start", "end"])
        w.writerows(rows)

    total = len(lines)
    print(f"Aligned {total} lines. Fallback lines: {fb_count} ({fb_count/total:.1%}).")

if __name__ == "__main__":
    main()
# end of csv_fixer_stable.py
