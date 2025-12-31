#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys
from .common import Paths, log, CYAN

def _run_step5(paths: Paths, slug: str, flags, *, privacy: str = "private", no_upload: bool = False):
    uploader = Path(__file__).resolve().parent / "5_upload.py"
    if not uploader.exists():
        log("UPLOAD", "5_upload.py missing, skipping", CYAN)
        return
    if no_upload:
        log("UPLOAD", "Skipping upload (--no-upload flag set)", CYAN)
        return
    cmd = [sys.executable, str(uploader), "--slug", slug, "--privacy", privacy]
    log("UPLOAD", " ".join(cmd))
    subprocess.run(cmd, check=True)

# explicit export
step5_deliver = _run_step5
__all__ = ["step5_deliver"]
# end of step5_deliver.py
