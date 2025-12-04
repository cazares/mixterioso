#!/usr/bin/env python3
# scripts/1_config.py
#
# NEVER HANGS. ALWAYS RETURNS JSON.
# Uses curses ONLY if stdin/stdout is a REAL TTY.
# Otherwise immediately falls back to safe text-mode.
#

import sys
import json
import os

MODES = [
    "vocals-100",
    "vocals-35",
    "vocals-25",
    "vocals-15",
    "vocals-0",
    "no-bass",
    "0-bass-0-vocals",
    "0-bass-35-vocals",
    "0-bass-25-vocals",
    "0-bass-15-vocals",
]

LANGUAGES = ["en", "es"]

DEFAULT_CFG = {
    "vocals": 100,
    "bass": 100,
    "guitar": 100,
    "drums": 100,
    "mode": "vocals-100",
    "language": "en"
}


# --------------------------------------------------------------------
# ALWAYS SAFE FALLBACK
# --------------------------------------------------------------------
def fallback():
    """Always returns JSON immediately — no hang possible."""
    sys.stdout.write(json.dumps(DEFAULT_CFG))
    sys.stdout.flush()
    return


# --------------------------------------------------------------------
# TRY CURSES (only if guaranteed safe)
# --------------------------------------------------------------------
def try_curses():
    try:
        import curses
    except:
        return None  # curses not available

    # If not real TTY → do NOT try curses
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    try:
        def menu(stdscr):
            curses.curs_set(0)
            stdscr.nodelay(False)
            stdscr.timeout(-1)

            idx_mix = 0
            idx_mode = 0
            idx_lang = 0
            section = 0

            mix_keys = ["vocals", "bass", "guitar", "drums"]
            mix = {k: 100 for k in mix_keys}

            while True:
                stdscr.clear()
                stdscr.addstr(1, 2, "Mixterioso Config (Curses Mode)", curses.A_BOLD)
                stdscr.addstr(3, 2, "Mix Levels (←/→ adjust):")

                # MIX VALUES
                for i, k in enumerate(mix_keys):
                    line = f"{k:10}: {mix[k]:3}%"
                    sel = curses.A_REVERSE if (section == 0 and idx_mix == i) else 0
                    stdscr.addstr(5 + i, 4, line, sel)

                # MODE
                stdscr.addstr(10, 2, "Mode:")
                for i, m in enumerate(MODES):
                    sel = curses.A_REVERSE if (section == 1 and idx_mode == i) else 0
                    stdscr.addstr(12 + i, 4, m, sel)

                # LANGUAGE
                base = 12 + len(MODES) + 2
                stdscr.addstr(base, 2, "Language:")
                for i, lang in enumerate(LANGUAGES):
                    sel = curses.A_REVERSE if (section == 2 and idx_lang == i) else 0
                    stdscr.addstr(base + 2 + i, 4, lang, sel)

                # CONFIRM
                confirm_y = base + 2 + len(LANGUAGES) + 2
                sel = curses.A_REVERSE if section == 3 else 0
                stdscr.addstr(confirm_y, 4, "CONFIRM", sel)

                key = stdscr.getch()

                if key == curses.KEY_UP:
                    if section == 0: idx_mix = (idx_mix - 1) % len(mix_keys)
                    elif section == 1: idx_mode = (idx_mode - 1) % len(MODES)
                    elif section == 2: idx_lang = (idx_lang - 1) % len(LANGUAGES)
                    else: section = 2

                elif key == curses.KEY_DOWN:
                    if section == 0: idx_mix = (idx_mix + 1) % len(mix_keys)
                    elif section == 1: idx_mode = (idx_mode + 1) % len(MODES)
                    elif section == 2: idx_lang = (idx_lang + 1) % len(LANGUAGES)
                    else: section = 3

                elif key == curses.KEY_LEFT and section == 0:
                    mix[mix_keys[idx_mix]] = max(0, mix[mix_keys[idx_mix]] - 5)

                elif key == curses.KEY_RIGHT and section == 0:
                    mix[mix_keys[idx_mix]] = min(100, mix[mix_keys[idx_mix]] + 5)

                elif key in (10, 13):  # ENTER
                    if section < 3:
                        section += 1
                    else:
                        return {
                            **mix,
                            "mode": MODES[idx_mode],
                            "language": LANGUAGES[idx_lang]
                        }

        return curses.wrapper(menu)

    except Exception:
        return None


# --------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------
def main():
    cfg = try_curses()
    if cfg is None:
        fallback()
    else:
        sys.stdout.write(json.dumps(cfg))
        sys.stdout.flush()


if __name__ == "__main__":
    main()

# end of 1_config.py
