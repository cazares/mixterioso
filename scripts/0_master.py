#!/usr/bin/env python3
import sys, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
MP3_DIR = ROOT / "mp3s"
MIXES_DIR = ROOT / "mixes"
PY = sys.executable

def usage():
    print('Usage:')
    print('  python3 scripts/0_master.py')
    print('  python3 scripts/0_master.py 245')
    print('  python3 scripts/0_master.py --query "Artist - Title"')
    print('  python3 scripts/0_master.py --query "Artist - Title" 45')
    sys.exit(1)

def has_audio(slug: str) -> bool:
    if (MIXES_DIR / f"{slug}.wav").exists():
        return True
    if (MP3_DIR / f"{slug}.mp3").exists():
        return True
    return False

def slug_from_query(query: str) -> str:
    title = query.split("-", 1)[-1].strip()
    return title.lower().replace(" ", "_")

def main():
    args = sys.argv[1:]
    query = None
    steps = None

    if "--query" in args:
        qi = args.index("--query")
        try:
            query = args[qi + 1]
        except IndexError:
            usage()
        args = args[:qi] + args[qi+2:]

    if args:
        if not args[0].isdigit():
            usage()
        steps = [int(c) for c in args[0] if c.isdigit()]

    if not steps:
        print("Pick steps to run (e.g. 12345):")
        raw = input("> ").strip()
        if not raw:
            print("No steps selected")
            return
        steps = [int(c) for c in raw if c.isdigit()]

    slug = None

    if query:
        slug = slug_from_query(query)

    for step in steps:
        if step == 1:
            if not query:
                query = input("Artist - Title: ").strip()
                slug = slug_from_query(query)
            subprocess.run([PY, str(SCRIPTS / "1_fetch.py"), "--query", query], check=True)

        elif step == 2:
            subprocess.run([PY, str(SCRIPTS / "2_stems.py"), "--slug", slug], check=True)

        elif step == 3:
            subprocess.run([PY, str(SCRIPTS / "3_timing.py"), "--slug", slug], check=True)

        elif step == 4:
            if not slug or not has_audio(slug):
                sys.exit("Cannot run Step 4: no audio available (mp3 or mix required)")
            subprocess.run([PY, str(SCRIPTS / "4_mp4.py"), "--slug", slug], check=True)

        elif step == 5:
            if not slug:
                sys.exit("Cannot run Step 5: slug required")
            subprocess.run([PY, str(SCRIPTS / "5_upload.py"), "--slug", slug], check=True)

    print("[MAIN] Pipeline finished.")

if __name__ == "__main__":
    main()
# end of 0_master.py
