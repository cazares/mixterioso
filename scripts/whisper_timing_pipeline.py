#!/usr/bin/env python3
"""
whisper_timing_pipeline.py
"""

import argparse, json, subprocess, sys, tempfile, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KNOWN_DEMUCS_LATENCIES = {
    "htdemucs": 0.18,
    "htdemucs_6s": 0.18,
    "htdemucs_ft": 0.18,
}

def run_cmd(cmd, cwd=None, capture=False):
    p = subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True)
    if capture:
        return p.returncode, p.stdout + p.stderr
    return p.returncode, ""

def have_program(name: str) -> bool:
    return subprocess.run(["which", name], capture_output=True).returncode == 0

def ensure_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def run_demucs(audio: str, model: str, out_dir: str) -> Optional[str]:
    if not have_program("demucs"):
        print("[demucs] not installed. skipping.", file=sys.stderr)
        return None
    print(f"[demucs] separating with model {model} …")
    code, out = run_cmd(["demucs", "-n", model, "-o", out_dir, audio], capture=True)
    if code != 0:
        print("[demucs] failed:", out, file=sys.stderr)
        return None
    out_path = Path(out_dir)
    cands = list(out_path.rglob("vocals.*")) or list(out_path.rglob("vocals.wav"))
    if not cands:
        print("[demucs] no vocals.*", file=sys.stderr)
        return None
    v = str(cands[0])
    print(f"[demucs] got vocals at {v}")
    return v

def ffmpeg_trim(in_path: str, out_path: str, offset_s: float) -> str:
    print(f"[ffmpeg] trimming {offset_s:.3f}s …")
    code, out = run_cmd(
        ["ffmpeg", "-y", "-ss", f"{offset_s:.3f}", "-i", in_path, "-acodec", "pcm_s16le", out_path],
        capture=True,
    )
    if code != 0:
        print("[ffmpeg] trim failed:", out, file=sys.stderr)
        return in_path
    return out_path

def ffmpeg_to_mono16k_loudnorm(in_path: str, out_path: str) -> str:
    print("[ffmpeg] creating mono, 16kHz, loudnorm version …")
    af = "pan=mono|c0=0.5*c0+0.5*c1,loudnorm=I=-16:LRA=11:TP=-1.5"
    code, out = run_cmd(
        ["ffmpeg", "-y", "-i", in_path, "-af", af, "-ar", "16000", "-ac", "1", out_path],
        capture=True,
    )
    if code != 0:
        print("[ffmpeg] mono/loudnorm failed:", out, file=sys.stderr)
        return in_path
    return out_path

def run_whisper_python(audio_path, model_name, language, prompt):
    try:
        import whisper  # type: ignore
    except ImportError:
        return None
    print(f"[whisper(py)] loading {model_name} …")
    model = whisper.load_model(model_name)
    print("[whisper(py)] transcribing …")
    kw: Dict[str, Any] = {"word_timestamps": True, "condition_on_previous_text": False}
    if language:
        kw["language"] = language
    if prompt:
        kw["initial_prompt"] = prompt
    return model.transcribe(audio_path, **kw)

def run_whisper_cli(audio_path, model_name, language, prompt):
    if not have_program("whisper"):
        print("[whisper(cli)] not installed.", file=sys.stderr)
        return None
    tmpdir = tempfile.mkdtemp(prefix="whisper_cli_")
    cmd = [
        "whisper",
        audio_path,
        "--model", model_name,
        "--task", "transcribe",
        "--output_dir", tmpdir,
        "--output_format", "json",
        "--condition_on_previous_text", "False",
        "--temperature", "0.0",
        "--beam_size", "5",
        "--best_of", "5",
    ]
    if language:
        cmd += ["--language", language]
    if prompt:
        cmd += ["--initial_prompt", prompt]
    print("[whisper(cli)] running …")
    code, out = run_cmd(cmd, capture=True)
    if code != 0:
        print("[whisper(cli)] failed:", out, file=sys.stderr)
        return None
    js = list(Path(tmpdir).glob("*.json"))
    if not js:
        print("[whisper(cli)] no json.", file=sys.stderr)
        return None
    return json.loads(js[0].read_text(encoding="utf-8"))

def try_whisper(audio_path, model_name, language, prompt):
    res = run_whisper_python(audio_path, model_name, language, prompt)
    if res is not None:
        return res
    res = run_whisper_cli(audio_path, model_name, language, prompt)
    if res is not None:
        return res
    raise RuntimeError("no whisper backend")

def try_whisperx_align(audio_path, whisper_result, language):
    try:
        import torch  # type: ignore
        import whisperx  # type: ignore
    except ImportError:
        print("[whisperx] not installed. skipping.", file=sys.stderr)
        return None
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[whisperx] loading on {device} …")
    if language is None:
        language = whisper_result.get("language", "en")
    model = whisperx.load_model("large-v3", device)
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    return whisperx.align(result["segments"], align_model, metadata, audio, device)

def extract_words_from_whisper(result):
    words = []
    for seg in result.get("segments", []):
        seg_start = seg.get("start", 0.0)
        seg_end = seg.get("end", 0.0)
        if seg.get("words"):
            for w in seg["words"]:
                words.append({
                    "word": w.get("word", "").strip(),
                    "start": float(w.get("start", seg_start)),
                    "end": float(w.get("end", seg_end)),
                    "conf": float(w.get("probability", seg.get("avg_logprob", 0.0))),
                })
        else:
            txt = seg.get("text", "").strip()
            if txt:
                words.append({
                    "word": txt,
                    "start": float(seg_start),
                    "end": float(seg_end),
                    "conf": float(seg.get("avg_logprob", 0.0)),
                })
    return words

def apply_offset(words, offset):
    for w in words:
        w["start"] = max(0.0, w["start"] + offset)
        w["end"] = max(0.0, w["end"] + offset)

def write_csv(words, path):
    ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write("word,start,end,conf\n")
        for w in words:
            f.write(f"{w['word']},{w['start']:.3f},{w['end']:.3f},{w['conf']:.4f}\n")
    print(f"[out] {path}")

def write_lines_csv(lines, path):
    ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write("line,start\n")
        for L in lines:
            t = L["line"].strip()
            if not t:
                continue
            f.write(f"{t},{L['start']:.3f}\n")
    print(f"[out] {path}")

def write_lines_txt(lines, path):
    ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        for L in lines:
            t = L["line"].strip()
            if not t:
                continue
            f.write(t + "\n")
    print(f"[out] {path}")

def write_json(result, path):
    ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[out] {path}")

_WORD_RE = re.compile(r"[a-z0-9áéíóúüñ']+", re.IGNORECASE)

def _norm_token(s: str) -> str:
    m = _WORD_RE.findall(s.lower())
    return m[0] if m else ""

def _is_header_line(line: str) -> bool:
    return "//by//" in line.lower()

def _search_range(W, words, tokens, start_i, end_i, skip_max):
    best = None
    upper = min(end_i, len(W))
    for k in range(start_i, upper):
        if W[k] != tokens[0]:
            continue
        m = 1
        j = k + 1
        last_match = k
        while m < len(tokens) and j < len(W):
            hopped = 0
            while j < len(W) and W[j] != tokens[m] and hopped < skip_max:
                j += 1
                hopped += 1
            if j < len(W) and W[j] == tokens[m]:
                last_match = j
                m += 1
                j += 1
            else:
                break
        score = m / len(tokens)
        if best is None or score > best[0]:
            best = (score, k, last_match)
            if score >= 0.98:
                break
    return best

def align_txt_lines_to_words(
    words: List[Dict[str, Any]],
    lyrics_lines: List[str],
    search_ahead: int = 400,
    skip_max: int = 6,
    min_cover: float = 0.5,
    local_dt: float = 6.0,
    max_jump: float = 8.0,
) -> List[Dict[str, Any]]:
    W = [_norm_token(w["word"]) for w in words]
    out: List[Dict[str, Any]] = []
    wi = 0
    last_ts = 0.0
    for line in lyrics_lines:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        tokens = [_norm_token(t) for t in line.split() if _norm_token(t)]
        if not tokens:
            out.append({"line": line, "start": last_ts})
            continue
        # tight window near last_ts
        tight_end_ts = last_ts + local_dt
        tight_end_idx = wi
        while tight_end_idx < len(words) and words[tight_end_idx]["start"] <= tight_end_ts:
            tight_end_idx += 1
        best = _search_range(W, words, tokens, wi, tight_end_idx, skip_max)
        # wide window
        if not best or best[0] < min_cover:
            wide_end_idx = min(len(W), wi + search_ahead)
            best = _search_range(W, words, tokens, wi, wide_end_idx, skip_max)
        if best and best[0] >= min_cover:
            _, si, ei = best
            ts = words[si]["start"]
            # clamp big forward jumps
            if ts - last_ts > max_jump:
                # ignore that far match, snap to next real word
                snap_ts = last_ts
                for ww in words:
                    if ww["start"] > last_ts:
                        snap_ts = ww["start"]
                        break
                out.append({"line": line, "start": snap_ts})
                last_ts = snap_ts
            else:
                out.append({"line": line, "start": ts})
                last_ts = ts
                if _is_header_line(line):
                    wi = 0
                else:
                    wi = min(ei + 1, si + search_ahead)
        else:
            # snap forward to next actual word
            snap_ts = last_ts
            for ww in words:
                if ww["start"] > last_ts:
                    snap_ts = ww["start"]
                    break
            out.append({"line": line, "start": snap_ts})
            last_ts = snap_ts
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--artist")
    ap.add_argument("--title")
    ap.add_argument("--use-demucs", action="store_true")
    ap.add_argument("--demucs-model", default="htdemucs_6s")
    ap.add_argument("--demucs-latency", type=float, default=None)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language")
    ap.add_argument("--out-json")
    ap.add_argument("--out-csv")
    ap.add_argument("--out-lines-csv")
    ap.add_argument("--out-lines-txt")
    ap.add_argument("--gap-threshold", type=float, default=0.60)
    ap.add_argument("--max-chars", type=int, default=32)
    ap.add_argument("--no-whisperx", action="store_true")
    ap.add_argument("--lyrics-txt")
    args = ap.parse_args()

    if not Path(args.audio).exists():
        print(f"[err] audio not found: {args.audio}", file=sys.stderr)
        sys.exit(1)

    workdir = tempfile.mkdtemp(prefix="whisper_timing_")
    print(f"[tmp] {workdir}")

    demucs_offset = 0.0
    audio_for_whisper = args.audio
    if args.use_demucs:
        d_out = str(Path(workdir) / "demucs_out")
        vocals = run_demucs(args.audio, args.demucs_model, d_out)
        if vocals:
            latency = args.demucs_latency if args.demucs_latency is not None else KNOWN_DEMUCS_LATENCIES.get(args.demucs_model, 0.0)
            if latency > 0:
                trimmed = str(Path(workdir) / "vocals_trimmed.wav")
                vocals = ffmpeg_trim(vocals, trimmed, latency)
                demucs_offset = latency
            audio_for_whisper = vocals
        else:
            print("[demucs] using original audio.", file=sys.stderr)

    proc_audio = str(Path(workdir) / "mono16k.wav")
    audio_for_whisper = ffmpeg_to_mono16k_loudnorm(audio_for_whisper, proc_audio)

    prompt = []
    if args.artist:
        prompt.append(f"Artist: {args.artist}.")
    if args.title:
        prompt.append(f"Song: {args.title}.")
    prompt.append("Do not describe music. Do not output [instrumental]. Output lyrics only.")
    initial_prompt = " ".join(prompt)

    whisper_res = try_whisper(audio_for_whisper, args.model, args.language, initial_prompt)
    aligned_res = None
    if not args.no_whisperx:
        aligned_res = try_whisperx_align(audio_for_whisper, whisper_res, args.language)

    result = aligned_res if aligned_res is not None else whisper_res
    words = extract_words_from_whisper(result)

    if demucs_offset != 0.0:
        print(f"[offset] applying {demucs_offset:.3f}s")
        apply_offset(words, demucs_offset)

    if args.out_csv:
        write_csv(words, args.out_csv)

    if args.lyrics_txt and Path(args.lyrics_txt).exists():
        raw_lines = Path(args.lyrics_txt).read_text(encoding="utf-8").splitlines()
        lines = align_txt_lines_to_words(words, raw_lines)
    else:
        lines = group_words_to_lines(words, args.gap_threshold, args.max_chars)

    if args.out_lines_csv:
        write_lines_csv(lines, args.out_lines_csv)

    out_txt = args.out_lines_txt
    if out_txt is None and args.out_lines_csv and "auto_lyrics/" in args.out_lines_csv:
        out_txt = args.out_lines_csv.rsplit(".", 1)[0] + ".txt"
    if out_txt:
        write_lines_txt(lines, out_txt)

    if args.out_json:
        write_json(result, args.out_json)

    print("[preview]")
    for w in words[:15]:
        print(f"{w['start']:.3f}-{w['end']:.3f}: {w['word']} ({w['conf']:.3f})")
    print("[done]")

if __name__ == "__main__":
    main()
# end of whisper_timing_pipeline.py
