#!/usr/bin/env python3
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sys
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import subprocess

from mix_utils import (
    log, RESET, CYAN, GREEN, YELLOW, BLUE, RED,
    slugify, PATHS,
)

MP3_DIR    = PATHS["mp3"]
SEPARATED  = PATHS["separated"]
MIXES_DIR  = PATHS["mixes"]
MIXES_DIR.mkdir(exist_ok=True)

TRACKS = ["vocals", "bass", "drums", "other"]


# ============================================
# Mix UI
# ============================================
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
            stdscr.addstr(0, 0, title[: w - 1])
            stdscr.attroff(curses.A_BOLD)
            stdscr.attroff(curses.color_pair(1))

            stdscr.attron(curses.color_pair(2))
            stdscr.addstr(
                1,
                0,
                "[UP/DOWN] select  [LEFT/RIGHT] -/+5%  [0] mute  [1] 100%  "
                "[ENTER] save  [q] abort",
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
                    stdscr.addstr(row, 0, line[: w - 1])
                    stdscr.attroff(curses.A_BOLD)
                    stdscr.attroff(curses.color_pair(3))
                else:
                    stdscr.attron(curses.color_pair(4))
                    stdscr.addstr(row, 0, line[: w - 1])
                    stdscr.attroff(curses.color_pair(4))

            if last_msg:
                stdscr.attron(curses.color_pair(2))
                stdscr.addstr(h - 2, 0, last_msg[: w - 1])
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

            tname = TRACKS[selected]

            if ch == curses.KEY_LEFT:
                volumes[tname] = max(0.0, min(2.0, volumes[tname] - 0.05))
                last_msg = f"{tname} → {int(round(volumes[tname] * 100))}%"
                continue
            if ch == curses.KEY_RIGHT:
                volumes[tname] = max(0.0, min(2.0, volumes[tname] + 0.05))
                last_msg = f"{tname} → {int(round(volumes[tname] * 100))}%"
                continue
            if ch == ord("0"):
                volumes[tname] = 0.0
                last_msg = f"{tname} muted"
                continue
            if ch == ord("1"):
                volumes[tname] = 1.0
                last_msg = f"{tname} → 100%"
                continue

    import curses
    curses.wrapper(ui)

    if not state["confirmed"]:
        raise SystemExit("Mix UI aborted.")

    return state["volumes"]


# ============================================
# Demucs logic
# ============================================
def run_demucs(slug: str, model: str):
    mp3 = MP3_DIR / f"{slug}.mp3"
    if not mp3.exists():
        raise SystemExit(f"MP3 not found: {mp3}")

    log("DEMUX", f"Running Demucs model={model}", BLUE)
    cmd = ["demucs", "-n", model, str(mp3)]
    subprocess.run(cmd, check=True)
    log("DEMUX", "Finished extracting stems.", GREEN)


def warn_overwrite_stems(slug: str, model: str):
    d = SEPARATED / model / slug
    if not d.exists():
        return
    wavs = list(d.glob("*.wav"))
    if not wavs:
        return

    print()
    print(f"{YELLOW}WARNING: Re-running Demucs will overwrite:{RESET}")
    for w in wavs:
        print(f"   • {w}")
    print()

    try:
        ok = input("Proceed? [y/N]: ").strip().lower()
    except EOFError:
        ok = ""

    if ok not in ("y", "yes"):
        raise SystemExit("Cancelled to avoid overwriting stems.")


def choose_stems(slug: str, model: str) -> str:
    d = SEPARATED / model / slug
    if not d.exists():
        log("STEMS", "No stems found → running Demucs.", YELLOW)
        run_demucs(slug, model)
        return model

    print()
    print(f"{CYAN}Stems already exist for slug '{slug}' (model '{model}').{RESET}")
    print()
    print("Reuse stems when:")
    print("  • You only want to change mix/volumes")
    print("  • You've already extracted stems earlier")
    print("  • You want to avoid re-running Demucs (slow)")
    print()
    print("Do NOT reuse stems when:")
    print("  • You replaced the mp3 with a new version")
    print("  • The previous extraction had issues")
    print("  • You want a clean fresh separation")
    print()

    try:
        ans = input("Reuse existing stems? [Y/n]: ").strip().lower()
    except EOFError:
        ans = "y"

    if ans in ("", "y", "yes"):
        log("STEMS", "Reusing existing stems", GREEN)
        return model

    warn_overwrite_stems(slug, model)
    run_demucs(slug, model)
    return model


# ============================================
# Load stems
# ============================================
def load_stems(slug: str, model: str) -> dict:
    d = SEPARATED / model / slug
    stems = {}
    for t in TRACKS:
        p = d / f"{t}.wav"
        if not p.exists():
            raise SystemExit(f"Missing stem: {p}")
        stems[t] = p
    return stems


# ============================================
# Warn before overwriting final WAV
# ============================================
def warn_overwrite_wav(path: Path):
    if not path.exists():
        return
    print()
    print(f"{YELLOW}WARNING: Overwriting mix file:{RESET}")
    print(f"   • {path}")
    print()
    try:
        ok = input("Overwrite? [y/N]: ").strip().lower()
    except EOFError:
        ok = ""
    if ok not in ("y", "yes"):
        raise SystemExit("Cancelled to avoid overwriting output WAV.")


# ============================================
# Render final mix
# ============================================
def render_mix(stems: dict, volumes: dict, out_wav: Path):
    warn_overwrite_wav(out_wav)

    filter_parts = []
    labels = []
    inputs = []

    # Each WAV file is a separate input: index = input number.
    for idx, t in enumerate(TRACKS):
        p = stems[t]
        v = float(volumes.get(t, 1.0))
        inputs.append(p)

        # Correct audio stream reference:
        in_label  = f"{idx}:a:0"     # <-- FIXED (explicit audio stream #0)
        out_label = f"a{idx}"

        filter_parts.append(f"[{in_label}]volume={v:.3f}[{out_label}]")
        labels.append(f"[{out_label}]")

    # amix all labeled outputs
    amix = "".join(labels) + f"amix=inputs={len(TRACKS)}:normalize=0[mix]"
    filter_complex = ";".join(filter_parts + [amix])

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[mix]",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]

    log("FFMPEG", " ".join(cmd), BLUE)
    subprocess.run(cmd, check=True)
    log("MIX", f"Wrote {out_wav}", GREEN)

# ============================================
# MAIN
# ============================================
def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Super simple Demucs 4-stem mixer")
    p.add_argument("--mp3", required=True, help="Original mp3 file")
    p.add_argument("--model", default="htdemucs")

    # NEW FLAGS (Option A)
    p.add_argument("--no-ui", action="store_true", help="Skip UI and use provided volumes")
    p.add_argument("--vocals", type=float, help="Volume for vocals (0.0–2.0)")
    p.add_argument("--bass", type=float, help="Volume for bass (0.0–2.0)")
    p.add_argument("--drums", type=float, help="Volume for drums (0.0–2.0)")
    p.add_argument("--other", type=float, help="Volume for other (0.0–2.0)")

    args = p.parse_args(argv or sys.argv[1:])

    mp3_path = Path(args.mp3).resolve()
    if not mp3_path.exists():
        raise SystemExit(f"MP3 not found: {mp3_path}")

    slug = slugify(mp3_path.stem)

    # 1) UI unless --no-ui
    if args.no_ui:
        volumes = {t: 1.0 for t in TRACKS}
        for t in TRACKS:
            v = getattr(args, t)
            if v is not None:
                volumes[t] = max(0.0, min(2.0, float(v)))
        log("VOLUMES", f"Using CLI volumes: {volumes}", GREEN)
    else:
        volumes = mix_ui(slug, args.model)

    # 2) Decide stem source
    model_used = choose_stems(slug, args.model)

    # 3) Load stems
    stems = load_stems(slug, model_used)

    # 4) Mix
    out_wav = MIXES_DIR / f"{slug}.wav"
    render_mix(stems, volumes, out_wav)

    print()
    print(f"{GREEN}Done!{RESET} Mixed file written to: {out_wav}")


if __name__ == "__main__":
    main()

# end of 2_stems.py