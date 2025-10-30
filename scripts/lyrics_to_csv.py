#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, os, re, shutil, subprocess, sys, tempfile, json
from typing import List, Dict, Tuple

# --- Utilities ---------------------------------------------------------------

def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def has_cmd(name: str) -> bool:
    return shutil.which(name) is not None

def norm_tokens(s: str) -> List[str]:
    # normalize text into comparable tokens (keep a-z, 0-9, apostrophes)
    return re.findall(r"[a-z0-9']+", s.lower())

def read_lyrics(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [ln.rstrip() for ln in f if ln.strip()]

def to_line_start_csv(rows: List[Tuple[str,float,float]]) -> List[Tuple[str,float]]:
    return [(line, start) for (line, start, _end) in rows]

def ffmpeg_preconvert(in_audio: str) -> Tuple[str, str]:
    """
    Always pre-convert to 16k mono WAV to avoid ffmpeg pipe warnings and ensure consistent decoding.
    Returns (wav_path, cleanup_dir)
    """
    if not has_cmd("ffmpeg"):
        die("ffmpeg not found. On macOS: brew install ffmpeg")
    tmpdir = tempfile.mkdtemp(prefix="alignwav_")
    out_wav = os.path.join(tmpdir, "audio_16k_mono.wav")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", in_audio, "-ac", "1", "-ar", "16000", out_wav]
    subprocess.run(cmd, check=True)
    return out_wav, tmpdir

def coverage_report(rows: List[Tuple[str,float,float]]) -> Tuple[int,int]:
    pinned = sum(1 for _, s, e in rows if abs(s - e) < 1e-6)
    return len(rows), pinned

# --- Matching core (robust, bounded, skip-tolerant) -------------------------

def words_from_stable(result) -> List[Dict]:
    words = []
    for seg in result.to_dict().get("segments", []):
        for w in seg.get("words", []):
            if w.get("word") and isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)):
                words.append({"word": w["word"], "start": float(w["start"]), "end": float(w["end"])})
    return words

def assign_lines_robust(words: List[Dict], lines: List[str],
                        start_wi: int = 0, search_ahead: int = 400,
                        skip_max: int = 6, min_cover: float = 0.60
                        ) -> List[Tuple[str,float,float]]:
    """
    Map each lyric line to the best span in the ASR word stream, without
    consuming everything on a miss. Bounded lookahead + limited skips.
    Returns [(line, start, end), ...]; pinned lines have start==end.
    """
    W = [ (norm_tokens(w["word"]) or [""])[0] for w in words ]
    out, wi = [], max(0, start_wi)

    for line in lines:
        toks = norm_tokens(line)
        if not toks:
            prev_end = out[-1][2] if out else 0.0
            out.append((line, round(prev_end,3), round(prev_end,3)))
            continue

        best = None  # (score, k, last_idx)
        end_window = min(len(W), wi + search_ahead)
        for k in range(wi, end_window):
            if W[k] != toks[0]:
                continue
            # walk rest of tokens allowing up to skip_max between matches
            m, j, last = 1, k + 1, k
            while m < len(toks) and j < len(W):
                hopped = 0
                while j < len(W) and W[j] != toks[m] and hopped < skip_max:
                    j += 1; hopped += 1
                if j < len(W) and W[j] == toks[m]:
                    last = j; m += 1; j += 1
                else:
                    break
            score = m / max(1, len(toks))
            if (best is None) or (score > best[0]):
                best = (score, k, last)
                if score >= 0.98:
                    break

        if best and best[0] >= min_cover:
            _, k, last = best
            start = words[k]["start"]
            end   = words[last]["end"]
            out.append((line, round(start,3), round(end,3)))
            wi = min(last + 1, k + search_ahead)  # advance but keep window sane
        else:
            prev_end = out[-1][2] if out else 0.0
            out.append((line, round(prev_end,3), round(prev_end,3)))

    return out

# --- Plans A-D --------------------------------------------------------------

def planA_stable_align(wav_path: str, lines: List[str], model_name: str):
    import stable_whisper  # from stable-ts
    model = stable_whisper.load_model(model_name)
    res = model.align(wav_path, "\n".join(lines), language="en")
    words = words_from_stable(res)
    return assign_lines_robust(words, lines)

def planB_whisper_transcribe(wav_path: str, lines: List[str], model_name: str):
    import stable_whisper
    model = stable_whisper.load_model(model_name)
    res = model.transcribe(wav_path, language="en")
    words = words_from_stable(res)
    return assign_lines_robust(words, lines)

def planC_forcealign(wav_path: str, lines: List[str]):
    """
    Optional: only if 'forcealign' and its deps are present.
    If importing pydub raises audioop/pyaudioop issues on Python 3.13,
    instruct the user to `pip install audioop-lts` and skip this plan.
    """
    try:
        from forcealign import ForceAlign
        transcript = " ".join(lines)
        fa = ForceAlign(audio_file=wav_path, transcript=transcript)
        ws = fa.inference()  # word objects with .word / .time_start / .time_end
        words = [{"word": w.word, "start": float(w.time_start), "end": float(w.time_end)} for w in ws]
        return assign_lines_robust(words, lines)
    except Exception as e:
        msg = str(e)
        if "pyaudioop" in msg or "audioop" in msg:
            print("Plan C note: ForceAlign needs audioop on Python 3.13. Run: pip install audioop-lts")
        else:
            print(f"Plan C skipped: {e}")
        return None

def planD_cloud_stub(_wav_path: str, _lines: List[str]):
    """
    Placeholder: wire this to Google/AWS/Azure if you want.
    Keep returning None by default to avoid external calls.
    """
    return None

# --- Main Orchestration -----------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Align lyrics.txt to audio and emit CSV timings.")
    ap.add_argument("--audio", required=True, help="Path to MP3/WAV/whatever (will be pre-converted).")
    ap.add_argument("--lyrics", required=True, help="Path to plain-text lyrics (one line per on-screen line).")
    ap.add_argument("--out", required=True, help="Output CSV path.")
    ap.add_argument("--model", default="large-v3", help="stable-ts model (e.g., base, small, medium, large-v3).")
    ap.add_argument("--format", default="line_start_end", choices=["line_start_end","line_start"],
                    help="CSV columns: line,start,end (default) or line,start.")
    ap.add_argument("--min-cover", type=float, default=0.60, help="Fraction of tokens to accept a match.")
    ap.add_argument("--search-ahead", type=int, default=400, help="Word lookahead window.")
    ap.add_argument("--skip-max", type=int, default=6, help="Max skipped words allowed between matches.")
    args = ap.parse_args()

    if not os.path.exists(args.audio):
        die(f"Audio not found: {args.audio}")
    if not os.path.exists(args.lyrics):
        die(f"Lyrics not found: {args.lyrics}")

    lines = read_lyrics(args.lyrics)

    # Pre-convert to 16k mono WAV (avoids ffmpeg pipe noise and normalizes input).
    try:
        wav_path, tmpdir = ffmpeg_preconvert(args.audio)
    except subprocess.CalledProcessError:
        die("ffmpeg failed to decode input. Is the file readable?")
    except Exception as e:
        die(f"Failed to pre-convert audio: {e}")

    rows = None

    # --- Plan A: stable-ts align() of known text
    try:
        print(">>> Plan A: stable-ts align() …")
        rows = planA_stable_align(wav_path, lines, args.model)
        total, pinned = coverage_report(rows)
        print(f"Plan A result: {total} lines, pinned={pinned}")
        # If too many pinned, escalate
        if pinned > max(0, total // 10):  # >10% pinned? try another plan
            rows = None
    except Exception as e:
        print(f"Plan A failed: {e}")

    # --- Plan B: Whisper transcribe + robust mapping
    if rows is None:
        try:
            print(">>> Plan B: Whisper transcribe + mapping …")
            rows = planB_whisper_transcribe(wav_path, lines, args.model)
            total, pinned = coverage_report(rows)
            print(f"Plan B result: {total} lines, pinned={pinned}")
            if pinned > max(1, total // 5):  # >20% pinned? escalate
                rows = None
        except Exception as e:
            print(f"Plan B failed: {e}")

    # --- Plan C: ForceAlign (optional)
    if rows is None:
        print(">>> Plan C: ForceAlign (optional) …")
        rows = planC_forcealign(wav_path, lines)
        if rows:
            total, pinned = coverage_report(rows)
            print(f"Plan C result: {total} lines, pinned={pinned}")

    # --- Plan D: Cloud (stub)
    if rows is None:
        print(">>> Plan D: Cloud STT (stub) …")
        rows = planD_cloud_stub(wav_path, lines)

    # Cleanup temp WAV
    try:
        shutil.rmtree(tmpdir)
    except Exception:
        pass

    if rows is None:
        die("All plans failed to produce an alignment. Check earlier logs.")

    # Emit CSV
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if args.format == "line_start":
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["line","start"])
            for line, start, _end in rows:
                w.writerow([line, f"{start:.3f}"])
    else:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["line","start","end"])
            for line, start, end in rows:
                w.writerow([line, f"{start:.3f}", f"{end:.3f}"])

    total, pinned = coverage_report(rows)
    print(f"✅ Wrote {args.out}  |  {total} lines  |  pinned={pinned} ({pinned/total:.1%})")
    if pinned:
        print("Note: 'pinned' lines had low confidence and were set to previous end; "
              "you can re-run with --search-ahead 800 --skip-max 10 or try a larger model.")

if __name__ == "__main__":
    main()
# end of scripts/lyrics_to_csv.py
