
#!/usr/bin/env python3
import sys
from mix_utils import log, write_json, PATHS

def run():
    log("TIMING", "Launching timing review UI (placeholder)")
    # write default offset
    offset = -1.5
    write_json(PATHS["timings"] / "default.offset", {"offset": offset})

if __name__ == "__main__":
    run()
# end of 3_sync.py
