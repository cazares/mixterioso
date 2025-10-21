#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_ass_from_csv.py — fixed for proper karaoke overlay + clean text
"""

import csv, sys
from pathlib import Path

def seconds_to_ass(ts):
    m, s = divmod(float(ts), 60)
    return f"0:{int(m):02d}:{s:05.2f}".replace('.', ',')

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_ass_from_csv.py lyrics_timing.csv")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    out_path = csv_path.with_suffix(".ass")

    rows = []
    with open(csv_path, newline='', encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    font_name = "Helvetica Neue Bold"
    font_size = 140
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,5,50,50,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


    lines = []
    for i, row in enumerate(rows):
        start = seconds_to_ass(row["timestamp"])
        end = seconds_to_ass(rows[i + 1]["timestamp"]) if i + 1 < len(rows) else seconds_to_ass(float(row["timestamp"]) + 3)
        lyric = row["text"].strip()
        lyric = lyric.replace("\\N\\N", "\\N\\N")  # preserve double breaks
        lyric = lyric.replace("\\N", "\\N")        # keep single breaks intact
        lyric = lyric.replace(",", "，")            # avoid comma parsing bugs
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{lyric}")

    out_path.write_text(header + "\n".join(lines), encoding="utf-8")
    print(f"✅ Fixed ASS file written: {out_path}")

if __name__ == "__main__":
    main()

# end of generate_ass_from_csv.py
