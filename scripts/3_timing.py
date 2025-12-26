#!/usr/bin/env python3
import sys, csv, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mix_utils import PATHS, log, GREEN, YELLOW

TIMINGS_DIR = PATHS["timings"]

def lrc_to_csv(lrc, csvp):
    rows = []
    for line in lrc.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[(\d+):(\d+\.\d+)\](.*)", line)
        if not m:
            continue
        mins, secs, text = m.groups()
        t = int(mins)*60 + float(secs)
        rows.append((t, text.strip()))
    rows.sort()
    with csvp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index","time_secs","text"])
        for i,(t,txt) in enumerate(rows):
            w.writerow([i, f"{t:.3f}", txt])
    log("TIMING", f"Wrote {csvp}", GREEN)

def vtt_to_csv(vtt, csvp):
    rows=[]
    text=vtt.read_text(encoding="utf-8",errors="ignore")
    for block in re.split(r"\n\n+", text):
        lines=block.splitlines()
        if not lines or "-->" not in lines[0]:
            continue
        start=lines[0].split("-->")[0].strip()
        h,m,s=start.replace(",",".").split(":")
        t=int(h)*3600+int(m)*60+float(s)
        caption=" ".join(lines[1:]).strip()
        if caption:
            rows.append((t,caption))
    rows.sort()
    with csvp.open("w", newline="", encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["line_index","time_secs","text"])
        for i,(t,txt) in enumerate(rows):
            w.writerow([i,f"{t:.3f}",txt])
    log("TIMING", f"Wrote {csvp}", GREEN)

def main():
    slug=sys.argv[sys.argv.index("--slug")+1]
    csvp=TIMINGS_DIR/f"{slug}.csv"
    if csvp.exists():
        log("TIMING","CSV exists, skipping",GREEN)
        return
    lrc=TIMINGS_DIR/f"{slug}.lrc"
    vtt=TIMINGS_DIR/f"{slug}.vtt"
    if lrc.exists():
        log("TIMING","Using LRC",GREEN)
        lrc_to_csv(lrc,csvp); return
    if vtt.exists():
        log("TIMING","Using captions",GREEN)
        vtt_to_csv(vtt,csvp); return
    log("TIMING","Manual timing required",YELLOW)

if __name__=="__main__":
    main()
# end of 3_timing.py
