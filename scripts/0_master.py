#!/usr/bin/env python3
"""Backwards-compat wrapper.

Prefer scripts/main.py going forward.
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    main_path = Path(__file__).resolve().parent / "main.py"
    runpy.run_path(str(main_path), run_name="__main__")

# end of 0_master.py
