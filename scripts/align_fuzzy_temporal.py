#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_fuzzy_temporal.py — combines fuzzy + temporal smoothing
All lines get timestamps: fuzzy matched when possible, interpolated otherwise.
"""

import json, csv, re, sys
from pathlib import Path
from rapidfuzz import fuzz, process

def normalize(t): return re.sub(r"[^a-z0-9 ]+", "", t.lower())

def hybrid_align(json_path, txt_path, output_csv):
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    segs = raw["segments"] if isinstance(raw, dict) else raw
    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    norm_lines = [normalize(l) for l in lines]
    norm_segs  = [normalize(s.get("text","")) for s in segs]
    seg_starts = [float(s.get("start",0.0)) for s in segs]

    used_idxs = set()
    results = []
    for norm,line in zip(norm_lines,lines):
        match = process.extractOne(norm, norm_segs, scorer=fuzz.partial_ratio)
        if match and match[1]>60:
            idx = norm_segs.index(match[0])
            used_idxs.add(idx)
            start = seg_starts[idx]
        else:
            start = None
        results.append([line,start])

    # fill None timestamps via linear interpolation between known ones
    known = [(i,s) for i,(_,s) in enumerate(results) if s is not None]
    if not known:  # all failed
        for i in range(len(results)):
            results[i][1] = i*3.0
    else:
        for i,(line,start) in enumerate(results):
            if start is not None: continue
            prev = max([j for j,_ in known if j<i], default=None)
            nxt  = min([j for j,_ in known if j>i], default=None)
            if prev is not None and nxt is not None:
                t0,t1 = known[[j for j,_ in known].index(prev)][1], known[[j for j,_ in known].index(nxt)][1]
                frac=(i-prev)/(nxt-prev)
                start=t0+(t1-t0)*frac
            elif prev is not None:
                start=known[-1][1]+3.0*(i-prev)
            elif nxt is not None:
                start=max(0.0,known[0][1]-3.0*(nxt-i))
            else:
                start=i*3.0
            results[i][1]=start

    with output_csv.open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["line","start"])
        for line,start in results:
            w.writerow([line,f"{start:.3f}"])

    print(f"✅ Hybrid aligned {len(results)} lines → {output_csv.name}")

def main():
    import argparse
    ap=argparse.ArgumentParser(description="Hybrid fuzzy+temporal aligner for karaoke CSV")
    ap.add_argument("--json",required=True)
    ap.add_argument("--text",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    hybrid_align(Path(args.json),Path(args.text),Path(args.output))

if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(1)

# end of align_fuzzy_temporal.py
