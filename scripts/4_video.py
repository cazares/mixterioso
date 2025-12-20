
#!/usr/bin/env python3
import sys
from mix_utils import log

def run():
    mode = "--manual" in sys.argv
    offset = -1.5 if mode else 0
    log("VIDEO", f"Rendering mp4 with font=120 offset={offset}")

if __name__ == "__main__":
    run()
# end of 4_video.py
