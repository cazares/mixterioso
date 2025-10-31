#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, csv, re
import stable_whisper  # from stable-ts library


def norm_tokens(s: str):
    """Normalize a string to tokens: lowercase alphanumeric/apostrophe tokens."""
    return re.findall(r"[a-z0-9']+", s.lower())


def extract_words_from_result(res):
    """Extract a flat list of words with their start and end times from a Whisper result."""
    words = []
    for seg in res.to_dict().get("segments", []):
        for w in seg.get("words", []):
            if (
                w.get("word")
                and isinstance(w.get("start"), (int, float))
                and isinstance(w.get("end"), (int, float))
            ):
                words.append(
                    {
                        "word": w["word"],
                        "start": float(w["start"]),
                        "end": float(w["end"]),
                    }
                )
    return words


def assign_lines_robust(words, lines, start_wi=0, search_ahead=400, skip_max=6, min_cover=0.6):
    """
    Assign each lyric line a start and end timestamp by matching its words to the ASR word list.
    Allows skipping up to skip_max words between matches and requires a minimum coverage of line tokens.
    If a line can't be matched, it is assigned a zero-duration at the last known time (fallback).
    """
    # Prepare a list of normalized word tokens from ASR output
    W = [(norm_tokens(w["word"]) or [""])[0] for w in words]
    out = []
    wi = max(0, start_wi)
    fallback_count = 0
    for line in lines:
        tokens = norm_tokens(line)
        if not tokens:
            # Empty line (or only punctuation): assign it the previous line's end time
            prev_end = out[-1][2] if out else 0.0
            out.append([line, round(prev_end, 3), round(prev_end, 3)])
            continue
        best_match = None  # tuple (score, start_index, end_index in W)
        # Limit how far ahead to search for the first token to avoid excessive mismatches
        end_window = min(len(W), wi + search_ahead)
        for k in range(wi, end_window):
            if W[k] != tokens[0]:
                continue
            # Potential start match found at index k
            m = 1
            j = k + 1
            last_match_idx = k
            # Try to match subsequent tokens in line
            while m < len(tokens) and j < len(W):
                # Allow up to skip_max unmatched words between token matches
                hopped = 0
                while j < len(W) and W[j] != tokens[m] and hopped < skip_max:
                    j += 1
                    hopped += 1
                if j < len(W) and W[j] == tokens[m]:
                    last_match_idx = j
                    m += 1
                    j += 1
                else:
                    break
            score = m / len(tokens)  # proportion of tokens matched in sequence
            if best_match is None or score > best_match[0]:
                best_match = (score, k, last_match_idx)
                if score >= 0.98:  # near-perfect match, no need to search further
                    break
        if best_match and best_match[0] >= min_cover:
            _, start_idx, end_idx = best_match
            start_time = words[start_idx]["start"]
            end_time = words[end_idx]["end"]
            out.append([line, round(start_time, 3), round(end_time, 3)])
            # Move the search pointer forward, but not too far (ensures we don't skip matching far ahead)
            wi = min(end_idx + 1, start_idx + search_ahead)
        else:
            # No good match found: use fallback (line gets zero-duration at last known end time)
            prev_end = out[-1][2] if out else 0.0
            out.append([line, round(prev_end, 3), round(prev_end, 3)])
            fallback_count += 1
    return out, fallback_count


def drop_header_lines(lines):
    """
    Drop junk like:
    - "Title//by//Artist"
    - "Title/by//Artist"
    - lines that contain "//by//"
    Keep everything else.
    """
    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if "//by//" in s or "/by/" in s:
            continue
        # also kill very short "title by artist" style headers if they sneak in
        if " by " in low and len(s.split()) <= 8:
            continue
        cleaned.append(s)
    return cleaned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="Path to song audio file")
    ap.add_argument("--lyrics", required=True, help="Path to lyrics text file")
    ap.add_argument("--out", required=True, help="Output CSV file path")
    ap.add_argument(
        "--model",
        default="large-v3",
        help="Whisper model name (e.g. large-v3, medium, small, tiny)",
    )
    ap.add_argument(
        "--min-cover",
        type=float,
        default=0.6,
        help="Minimum fraction of line tokens that must align",
    )
    ap.add_argument(
        "--search-ahead",
        type=int,
        default=400,
        help="Word search window size for alignment",
    )
    ap.add_argument(
        "--skip-max",
        type=int,
        default=6,
        help="Max words to skip in alignment matching",
    )
    # for compatibility with gen_video.sh which passes --no-vad
    ap.add_argument(
        "--no-vad",
        action="store_true",
        help="accepted for compatibility; ignored",
    )
    args = ap.parse_args()

    # Load lyrics lines
    with open(args.lyrics, "r", encoding="utf-8") as f:
        lines = [l.rstrip() for l in f if l.strip()]

    # strip pipeline headers like "Title//by//Artist"
    lines = drop_header_lines(lines)

    # Load Whisper model (will use GPU/MPS if available, otherwise CPU)
    model = stable_whisper.load_model(args.model)
    # Perform alignment across the entire lyrics text
    # Note: source may be Spanish. If you want auto-language, set language=None.
    result = model.align(args.audio, "\n".join(lines), language="en")
    words = extract_words_from_result(result)

    # Assign timestamps to each lyric line
    rows, fb_count = assign_lines_robust(
        words,
        lines,
        start_wi=0,
        search_ahead=args.search_ahead,
        skip_max=args.skip_max,
        min_cover=args.min_cover,
    )

    # Write output CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start", "end"])
        writer.writerows(rows)
    total = len(lines)
    print(
        f"Aligned {total} lines. Fallback lines (no timing match): {fb_count} ({fb_count/total:.1%})."
    )


if __name__ == "__main__":
    main()
# end of align_to_csv.py
