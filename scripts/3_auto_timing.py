# scripts/3_auto_timing.py
# CLI wrapper: generate timings CSV from txt+mp3.
# Usage examples:
#   python3 scripts/3_auto_timing.py --slug OIxRRR3gS_E --mode smart --verbose
#   python3 scripts/3_auto_timing.py --txt txts/song.txt --audio mp3s/song.mp3 --timings timings/song.csv --mode naive

from __future__ import annotations
import argparse
from pathlib import Path
import sys

# local imports
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))  # ensure aligner is importable

from aligner import (  # type: ignore
    AlignConfig,
    align_txt_to_audio,
    align_txt_to_audio_smart,
    log, CYAN, GREEN, YELLOW, RED,
)

TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Auto-generate timings CSV from lyrics TXT + MP3.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--slug", type=str, help="Use txts/<slug>.txt and mp3s/<slug>.mp3; out → timings/<slug>.csv")
    src.add_argument("--txt", type=str, help="Path to lyrics .txt (explicit)")
    ap.add_argument("--audio", type=str, help="MP3 path (required if --txt is used)")
    ap.add_argument("--timings", type=str, help="Output CSV path (required if --txt is used)")
    ap.add_argument("--mode", choices=["naive", "smart"], default="naive", help="Alignment strategy")
    ap.add_argument("--pad-head", type=float, default=0.75)
    ap.add_argument("--pad-tail", type=float, default=0.75)
    ap.add_argument("--min-step", type=float, default=1.0)
    ap.add_argument("--max-step", type=float, default=6.0)
    ap.add_argument("--verbose", action="store_true", help="Verbose logs")
    return ap.parse_args(argv)

def main(argv=None) -> int:
    args = parse_args(argv)

    if args.slug:
        slug = args.slug.strip()
        txt_path = TXT_DIR / f"{slug}.txt"
        audio_path = MP3_DIR / f"{slug}.mp3"
        out_csv = TIMINGS_DIR / f"{slug}.csv"
    else:
        if not args.audio or not args.timings or not args.txt:
            print("--txt requires --audio and --timings", file=sys.stderr)
            return 2
        txt_path = Path(args.txt)
        audio_path = Path(args.audio)
        out_csv = Path(args.timings)

    # Validate
    if not txt_path.exists():
        print(f"Missing TXT: {txt_path}", file=sys.stderr); return 3
    if not audio_path.exists():
        print(f"Missing audio: {audio_path}", file=sys.stderr); return 4
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    cfg = AlignConfig(
        pad_head=args.pad_head,
        pad_tail=args.pad_tail,
        min_step=args.min_step,
        max_step=args.max_step,
    )

    log("ALIGN", f"txt={txt_path.name} audio={audio_path.name} → {out_csv.name} [{args.mode}]", CYAN)
    try:
        if args.mode == "smart":
            align_txt_to_audio_smart(txt_path, audio_path, out_csv, cfg, verbose=args.verbose)
        else:
            align_txt_to_audio(txt_path, audio_path, out_csv, cfg, verbose=args.verbose)
    except Exception as e:
        log("ALIGN", f"failed: {e}", RED)
        return 5

    log("ALIGN", f"OK → {out_csv}", GREEN)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

# end of 3_auto_timing.py
