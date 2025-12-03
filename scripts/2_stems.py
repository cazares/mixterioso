#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import subprocess

from mix_utils import (
    log, CYAN, GREEN, YELLOW, BLUE, RED,
    slugify, ask_yes_no,
    PATHS,
)

TRACKS = ["vocals", "bass", "drums", "other"]

# ─────────────────────────────────────────────
# Mix UI (unchanged)
# ─────────────────────────────────────────────
def mix_ui(slug: str, model: str) -> dict:
    import curses
    volumes = {t: 1.0 for t in TRACKS}
    state = {"volumes": volumes, "confirmed": False}

    def ui(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)

        selected = 0
        last_msg = ""

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            title = f"Mix UI for slug={slug}, model={model}"
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(0, 0, title[:w-1])
            stdscr.attroff(curses.A_BOLD)
            stdscr.attroff(curses.color_pair(1))

            stdscr.attron(curses.color_pair(2))
            stdscr.addstr(
                1, 0,
                "[UP/DOWN] sel  [LEFT/RIGHT] -/+5%  [0] mute  [1] 100%  [ENTER] save  [q] abort"
            )
            stdscr.attroff(curses.color_pair(2))

            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(3, 0, "Track".ljust(12) + "Volume")
            stdscr.attroff(curses.color_pair(4))

            for i, t in enumerate(TRACKS):
                pct = int(round(volumes[t] * 100))
                line = f"{t.title().ljust(12)}{pct:3d}%"
                row = 4 + i

                if i == selected:
                    stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
                    stdscr.addstr(row, 0, line[:w-1])
                    stdscr.attroff(curses.A_BOLD)
                    stdscr.attroff(curses.color_pair(3))
                else:
                    stdscr.attron(curses.color_pair(4))
                    stdscr.addstr(row, 0, line[:w-1])
                    stdscr.attroff(curses.color_pair(4))

            if last_msg:
                stdscr.attron(curses.color_pair(2))
                stdscr.addstr(h-2, 0, last_msg[:w-1])
                stdscr.attroff(curses.color_pair(2))

            stdscr.refresh()
            stdscr.timeout(200)
            ch = stdscr.getch()
            if ch == -1:
                continue

            if ch in (27, ord("q"), ord("Q")):
                state["confirmed"] = False
                return
            if ch in (10, 13):
                state["confirmed"] = True
                return

            if ch == curses.KEY_UP:
                selected = (selected - 1) % len(TRACKS); last_msg = ""; continue
            if ch == curses.KEY_DOWN:
                selected = (selected + 1) % len(TRACKS); last_msg = ""; continue

            tname = TRACKS[selected]
            if ch == curses.KEY_LEFT:
                volumes[tname] = max(0.0, min(2.0, volumes[tname] - 0.05))
                last_msg = f"{tname} → {int(round(volumes[tname] * 100))}%"
            elif ch == curses.KEY_RIGHT:
                volumes[tname] = max(0.0, min(2.0, volumes[tname] + 0.05))
                last_msg = f"{tname} → {int(round(volumes[tname] * 100))}%"
            elif ch == ord("0"):
                volumes[tname] = 0.0; last_msg = f"{tname} muted"
            elif ch == ord("1"):
                volumes[tname] = 1.0; last_msg = f"{tname} → 100%"

    import curses
    curses.wrapper(ui)
    if not state["confirmed"]:
        fatal("Mix UI aborted.")
    return state["volumes"]

# ─────────────────────────────────────────────
# LOAD STEM PATHS
# ─────────────────────────────────────────────
def load_stem_paths(stem_map: dict) -> dict:
    stems = {}
    for t in TRACKS:
        p = stem_map.get(t)
        if not p or not p.exists():
            fatal(f"Missing stem: {p}", "STEMS")
        stems[t] = p
    return stems

# ─────────────────────────────────────────────
# RENDER FINAL MIX
# ─────────────────────────────────────────────
def render_mix(stems: dict, volumes: dict, out_wav: Path):
    confirm_overwrite(out_wav, label="mix wav")

    filter_parts = []
    labels = []
    inputs = []

    for idx, t in enumerate(TRACKS):
        p = stems[t]
        v = float(volumes.get(t, 1.0))
        in_lab  = f"{idx}:a"
        out_lab = f"a{idx}"
        inputs.append(p)
        filter_parts.append(f"[{in_lab}]volume={v:.3f}[{out_lab}]")
        labels.append(f"[{out_lab}]")

    amix = "".join(labels) + f"amix=inputs={len(TRACKS)}:normalize=0[mix]"
    fc = ";".join(filter_parts + [amix])

    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", fc, "-map", "[mix]", "-c:a", "pcm_s16le", str(out_wav)]

    subprocess.run(cmd, check=True)
    log("MIX", f"Wrote {out_wav}", GREEN)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    ensure_pipeline_dirs()

    mp3 = choose_mp3()
    slug = slugify(mp3.stem)
    model = DEFAULT_DEMUCS_MODEL

    # First: mix UI
    volumes = mix_ui(slug, model)

    # Inspect existing stems
    stem_path = stems_dir(slug, model)
    status, stem_map = inspect_stems(stem_path)

    if status == "none":
        log("STEMS", "No stems found → running Demucs.", YELLOW)
        run_demucs(mp3)
        status, stem_map = inspect_stems(stem_path)

    elif status in ("partial", "all"):
        print()
        print(f"{CYAN}Stems already exist for slug '{slug}' (model '{model}'):{RESET}")
        for t in sorted(stem_map):
            print(f"  • {stem_map[t]}")
        print()
        if not ask_yes_no("Reuse these stems?", default_yes=True):
            run_demucs(mp3)
            status, stem_map = inspect_stems(stem_path)

    stems = load_stem_paths(stem_map)

    out_wav = PATHS["mixes"] / f"{slug}.wav"
    render_mix(stems, volumes, out_wav)

    print()
    log("DONE", f"Mixed file written to: {out_wav}", GREEN)

if __name__ == "__main__":
    main()
# end of 2_stems.py
