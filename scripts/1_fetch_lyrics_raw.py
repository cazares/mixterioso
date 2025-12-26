#!/usr/bin/env python3
import sys, json, requests
from mix_utils import PATHS
artist, title, slug = sys.argv[1:4]
out = PATHS["timings"]/f"{slug}.lrc"
try:
    r = requests.get("https://lrclib.net/api/get",
        params={"artist_name":artist,"track_name":title},timeout=10)
    if r.ok and r.json().get("syncedLyrics"):
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(r.json()["syncedLyrics"],encoding="utf-8")
        print(json.dumps({"src":"lyrics_raw","msg":"LRC fetched"}))
    else:
        print(json.dumps({"src":"lyrics_raw","msg":"No LRC"}))
except Exception:
    print(json.dumps({"src":"lyrics_raw","msg":"LRC error"}))
