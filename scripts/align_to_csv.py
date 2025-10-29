#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, re
import stable_whisper

def norm_tokens(s: str):
    # keep letters/digits/apostrophes, lowercased
    return re.findall(r"[a-z0-9']+", s.lower())

def extract_words_from_result(res):
    words = []
    for seg in res.to_dict().get("segments", []):
        for w in seg.get("words", []):
            if w.get("word") and isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)):
                words.append({"word": w["word"], "start": float(w["start"]), "end": float(w["end"])})
    return words

def assign_lines_robust(words, lines, start_wi=0, search_ahead=400, skip_max=6, min_cover=0.6):
    """
    Map each lyric line to a best-effort span in the ASR words.
    - search_ahead: how far ahead (in words) we scan to find the first token
    - skip_max: max non-matching words allowed between matched tokens
    - min_cover: fraction of line tokens that must be matched to accept
    """
    W = [ (norm_tokens(w["word"]) or [""]) [0] for w in words ]
    out, wi = [], max(0, start_wi)
    fallback_count = 0

    for line in lines:
        toks = norm_tokens(line)
        if not toks:
            # blank/symbol-only line; pin to previous end
            prev_end = out[-1][2] if out else 0.0
            out.append([line, round(prev_end,3), round(prev_end,3)])
            continue

        best = None  # (score, start_idx, end_idx)
        # bounded search for first-token candidates so we don't consume everything on a miss
        end_window = min(len(W), wi + search_ahead)
        for k in range(wi, end_window):
            if W[k] != toks[0]:
                continue
            # try to walk the rest of the tokens allowing small skips
            m = 1
            j = k + 1
            last_match_idx = k
            while m < len(toks) and j < len(W):
                # advance up to skip_max to find next token
                hopped = 0
                while j < len(W) and W[j] != toks[m] and hopped < skip_max:
                    j += 1
                    hopped += 1
                if j < len(W) and W[j] == toks[m]:
                    last_match_idx = j
                    m += 1
                    j += 1
                else:
                    break
            score = m / len(toks)
            if best is None or score > best[0]:
                best = (score, k, last_match_idx)
                # early exit if perfect (or near-perfect)
                if score >= 0.98:
                    break

        if best and best[0] >= min_cover:
            _, k, last = best
            start = words[k]["start"]
            end = words[last]["end"]
            out.append([line, round(start,3), round(end,3)])
            # advance global pointer to just past what we used (but not too far)
            wi = min(last + 1, k + search_ahead)
        else:
            # no decent match found: do NOT advance wi, just pin to previous end
            prev_end = out[-1][2] if out else 0.0
            out.append([line, round(prev_end,3), round(prev_end,3)])
            fallback_count += 1

    return out, fallback_count

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--min-cover", type=float, default=0.6)
    ap.add_argument("--search-ahead", type=int, default=400)
    ap.add_argument("--skip-max", type=int, default=6)
    args = ap.parse_args()

    with open(args.lyrics, "r") as f:
        lines = [l.rstrip() for l in f if l.strip()]

    model = stable_whisper.load_model(args.model)  # uses MPS on Apple Silicon if available
    # Align to the WHOLE lyrics text; Whisper aligns words across the full timeline
    res = model.align(args.audio, "\n".join(lines), language="en")
    words = extract_words_from_result(res)

    rows, fb = assign_lines_robust(
        words,
        lines,
        start_wi=0,
        search_ahead=args.search_ahead,
        skip_max=args.skip_max,
        min_cover=args.min_cover,
    )

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line", "start", "end"])
        w.writerows(rows)

    total = len(lines)
    print(f"Aligned {total} lines. Fallback (pinned) lines: {fb} ({fb/total:.1%}).")

if __name__ == "__main__":
    main()
# end of scripts/align_to_csv.py
