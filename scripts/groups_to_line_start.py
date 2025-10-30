#!/usr/bin/env python3
# scripts/groups_to_line_start.py
import argparse, json
from pathlib import Path
import pandas as pd

def from_csv(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    # expects columns: start,end,duration,text
    return pd.DataFrame({"line": df["text"], "start": df["start"].round(3)})

def from_json(p: Path) -> pd.DataFrame:
    j = json.loads(p.read_text(encoding="utf-8"))
    groups = j.get("groups", [])
    return pd.DataFrame({
        "line": [g["text"] for g in groups],
        "start": [round(float(g["start"]), 3) for g in groups],
    })

def convert_one(src: Path, out_dir: Path, prefix: str|None):
    out_dir.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".json":
        df = from_json(src)
    else:
        df = from_csv(src)
    name = (prefix or src.stem)
    out = out_dir / f"{name}.csv"
    df.to_csv(out, index=False)
    print(out)

def main():
    ap = argparse.ArgumentParser(description="Convert grouped phrases to line,start CSV.")
    ap.add_argument("--in", dest="inp", required=True, help="Input CSV or JSON (from whisper_only_karaoke.py)")
    ap.add_argument("--out-dir", default="lyrics_lined", help="Output directory")
    ap.add_argument("--prefix", default=None, help="Optional output filename prefix")
    ap.add_argument("--batch", action="store_true", help="Treat --in as directory and convert all CSV/JSON within")
    args = ap.parse_args()

    inp = Path(args.inp).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if args.batch:
        files = [p for p in inp.glob("*") if p.suffix.lower() in (".csv", ".json")]
        if not files:
            raise SystemExit("No CSV/JSON files found in directory.")
        for f in sorted(files):
            convert_one(f, out_dir, None)
    else:
        convert_one(inp, out_dir, args.prefix)

if __name__ == "__main__":
    main()

# end of scripts/groups_to_line_start.py
