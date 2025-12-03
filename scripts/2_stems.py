#!/usr/bin/env python3
import sys
from pathlib import Path

from scripts.mix_utils import (
    log, fatal, ask_yes_no,
    slugify, confirm_overwrite,
    PATHS, DEFAULT_DEMUCS_MODEL,
    choose_mp3, stems_dir, inspect_stems,
    run_demucs, clean_empty_dirs,
    CYAN, GREEN, YELLOW, BLUE
)

# CONSTANTS
TRACKS = ["vocals", "bass", "drums", "other"]
MODEL = DEFAULT_DEMUCS_MODEL


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
            stdscr.addstr(0, 0, title[: w-1])
            stdscr.attroff(curses.A_BOLD)
            stdscr.attroff(curses.color_pair(1))

            stdscr.attron(curses.color_pair(2))
            stdscr.addstr(1, 0,
                "[UP/DOWN] select  [LEFT/RIGHT] -/+5%  [0] mute  [1] 100%  "
                "[ENTER] save  [q] abort"
            )
            stdscr.attroff(curses.color_pair(2))

            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(3, 0, "Track".ljust(12) + "Volume")
            stdscr.attroff(curses.color_pair(4))

            for i, t in enumerate(TRACKS):
                pct = int(round(volumes[t] * 100))
                line = f"{t.title().ljust(12)}{pct:3d} %"
                row = 4 + i

                if i == selected:
                    stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
                    stdscr.addstr(row, 0, line[: w-1])
                    stdscr.attroff(curses.A_BOLD)
                    stdscr.attroff(curses.color_pair(3))
                else:
                    stdscr.attron(curses.color_pair(4))
                    stdscr.addstr(row, 0, line[: w-1])
                    stdscr.attroff(curses.color_pair(4))

            if last_msg:
                stdscr.attron(curses.color_pair(2))
                stdscr.addstr(h-2, 0, last_msg[: w-1])
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
                selected = (selected - 1) % len(TRACKS)
                last_msg = ""
                continue
            if ch == curses.KEY_DOWN:
                selected = (selected + 1) % len(TRACKS)
                last_msg = ""
                continue

            name = TRACKS[selected]

            if ch == curses.KEY_LEFT:
                volumes[name] = max(0.0, min(2.0, volumes[name] - 0.05))
                last_msg = f"{name} → {int(round(volumes[name] * 100))}%"
                continue
            if ch == curses.KEY_RIGHT:
                volumes[name] = max(0.0, min(2.0, volumes[name] + 0.05))
                last_msg = f"{name} → {int(round(volumes[name] * 100))}%"
                continue
            if ch == ord("0"):
                volumes[name] = 0.0
                last_msg = f"{name} muted"
                continue
            if ch == ord("1"):
                volumes[name] = 1.0
               	last_msg = f"{name} → 100%"
                continue

    import curses
    curses.wrapper(ui)

    if not state["confirmed"]:
        fatal("Mix UI aborted.", "MIX_UI")

    return state["volumes"]


# ─────────────────────────────────────────────
# LOAD STEMS
# ─────────────────────────────────────────────
def load_stems(mapping: dict[str, Path]) -> dict[str, Path]:
    for t, p in mapping.items():
        if not p.exists():
            fatal(f"Missing stem: {p}", "STEMS")
    return mapping


# ─────────────────────────────────────────────
# MIX (ffmpeg)
# ─────────────────────────────────────────────
def render_mix(stems: dict, volumes: dict, out_wav: Path):
    from subprocess import run

    confirm_overwrite(out_wav, label="mix WAV")

    filter_parts = []
    inputs = []
    labels = []

    for idx, t in enumerate(TRACKS):
        p = stems[t]
        v = float(volumes.get(t, 1.0))

        in_label = f"{idx}:a"
        out_label = f"a{idx}"

        inputs.append(p)
        filter_parts.append(f"[{in_label}]volume={v:.3f}[{out_label}]")
        labels.append(f"[{out_label}]")

    amix = "".join(labels) + f"amix=inputs={len(TRACKS)}:normalize=0[mix]"
    filter_comp = ";".join(filter_parts + [amix])

    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_comp,
        "-map", "[mix]",
        "-c:a", "pcm_s16le",
        str(out_wav)
    ]

    log("FFMPEG", "Rendering final mix...", BLUE)
    run(cmd, check=True)
    log("MIX", f"Wrote {out_wav}", GREEN)


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        fatal("2_stems.py takes no arguments.", "ARGS")

    mp3 = choose_mp3()
    slug = slugify(mp3.stem)

    sdir = stems_dir(slug, MODEL)
    status, mapping = inspect_stems(sdir, TRACKS)

    if status == "none":
        log("STEMS", f"No stems for '{slug}', running Demucs.", YELLOW)
        run_demucs(mp3, MODEL)
        status, mapping = inspect_stems(sdir, TRACKS)

    elif status == "partial":
        log("STEMS", f"Partial stems for '{slug}', re-running Demucs.", YELLOW)
        run_demucs(mp3, MODEL)
        status, mapping = inspect_stems(sdir, TRACKS)

    elif status == "all":
        print()
        print(f"{CYAN}Stems already exist for '{slug}'.{RESET}")
        print("Reuse them to tweak mix volumes.")
        print()
        if not ask_yes_no("Reuse existing stems?", default_yes=True):
            log("STEMS", "Re-running Demucs (existing will be replaced).", YELLOW)
            run_demucs(mp3, MODEL)
            status, mapping = inspect_stems(sdir, TRACKS)

    if status != "all":
        fatal("Demucs did not produce all 4 stems.", "STEMS")

    stems = load_stems(mapping)

    out_wav = PATHS["mixes"] / f"{slug}.wav"

    volumes = mix_ui(slug, MODEL)
    render_mix(stems, volumes, out_wav)

    clean_empty_dirs(PATHS["separated"])

    print()
    log("DONE", f"Mixed file written to: {out_wav}", GREEN)
    print()


if __name__ == "__main__":
    main()

# end of 2_stems.py
