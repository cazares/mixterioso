#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

import sys, os
PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT)

from scripts.mix_utils import load_existing_config, save_config

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
MIXES_DIR = BASE_DIR / "mixes"


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


# Strict 4-stem world: only real Demucs stems
TRACKS = ["vocals", "bass", "drums", "other"]
TRACK_LABELS = {
    "vocals": "Vocals",
    "bass": "Bass",
    "drums": "Drums",
    "other": "Other",
}


def profile_defaults(profile: str) -> dict:
    """
    Profile → linear volume multipliers for the 4 real stems.
    All values are in [0.0, 2.0].
    """
    if profile == "karaoke":
        # Pure karaoke: kill vocals, keep band flat
        return {
            "vocals": 0.0,
            "bass": 1.0,
            "drums": 1.0,
            "other": 1.0,
        }

    if profile == "car-karaoke":
        # Car karaoke: low-but-audible vocals over full band
        return {
            "vocals": 0.35,
            "bass": 1.0,
            "drums": 1.0,
            "other": 1.0,
        }

    if profile == "no-bass":
        # No bass: everything except the bass stem
        return {
            "vocals": 1.0,
            "bass": 0.0,
            "drums": 1.0,
            "other": 1.0,
        }

    if profile == "car-bass-karaoke":
        # Car bass karaoke: low vocals, no bass stem (sub-friendly mix)
        return {
            "vocals": 0.35,
            "bass": 0.0,
            "drums": 1.0,
            "other": 1.0,
        }

    # lyrics or unknown → flat 4-stem mix
    return {
        "vocals": 1.0,
        "bass": 1.0,
        "drums": 1.0,
        "other": 1.0,
    }


def mix_ui(slug: str, profile: str, model: str) -> dict:
    """
    curses-based UI for adjusting the 4 real Demucs stems.
    Returns a dict: { "vocals": float, "bass": float, "drums": float, "other": float }
    """
    import curses

    defaults = profile_defaults(profile)
    existing_vols, cfg_path = load_existing_config(slug, profile)
    volumes = defaults.copy()

    # Seed from existing config if user wants to reuse it
    if existing_vols:
        print()
        print(f"{YELLOW}Found existing mix config at {cfg_path}{RESET}")
        ans = input("Use previous settings as starting point? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            for k in TRACKS:
                if k in existing_vols:
                    try:
                        volumes[k] = float(existing_vols[k])
                    except Exception:
                        pass

    state = {"volumes": volumes, "confirmed": False}

    def ui(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)   # header
        curses.init_pair(2, curses.COLOR_YELLOW, -1)                 # instructions
        curses.init_pair(3, curses.COLOR_GREEN, -1)                  # selected
        curses.init_pair(4, curses.COLOR_CYAN, -1)                   # normal

        selected = 0
        last_msg = ""

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            title = f"Mix UI for slug={slug}, profile={profile}, model={model}"
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(0, 0, title[: w - 1])
            stdscr.attroff(curses.A_BOLD)
            stdscr.attroff(curses.color_pair(1))

            controls = (
                "[UP/DOWN] select  [LEFT/RIGHT] -/+5%  [0] mute  [1] 100%  "
                "[r] reset profile  [ENTER/s] save  [q/ESC] abort"
            )
            stdscr.attron(curses.color_pair(2))
            stdscr.addstr(1, 0, controls[: w - 1])
            stdscr.attroff(curses.color_pair(2))

            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(3, 0, "Track".ljust(16) + "Volume".ljust(10))
            stdscr.attroff(curses.color_pair(4))

            for i, tname in enumerate(TRACKS):
                vol = volumes.get(tname, 0.0)
                pct = int(round(vol * 100))
                line = f"{TRACK_LABELS[tname].ljust(16)}{str(pct).rjust(3)} %"
                row = 4 + i
                if row >= h - 2:
                    break
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

            footer = "[MIX] Use arrows to adjust, ENTER to confirm"
            stdscr.attron(curses.color_pair(1))
            stdscr.addstr(h - 1, 0, footer[: w - 1])
            stdscr.attroff(curses.color_pair(1))

            stdscr.refresh()
            stdscr.timeout(100)
            ch = stdscr.getch()
            if ch == -1:
                continue

            # Abort
            if ch in (27, ord("q"), ord("Q")):
                state["confirmed"] = False
                return

            # Save / confirm
            if ch in (10, 13, ord("s"), ord("S")):
                state["confirmed"] = True
                return

            # Navigation
            if ch == curses.KEY_UP:
                selected = (selected - 1) % len(TRACKS)
                last_msg = ""
                continue
            if ch == curses.KEY_DOWN:
                selected = (selected + 1) % len(TRACKS)
                last_msg = ""
                continue

            tname = TRACKS[selected]

            # Adjustments
            if ch == curses.KEY_LEFT:
                volumes[tname] = max(0.0, min(2.0, volumes.get(tname, 0.0) - 0.05))
                last_msg = f"{TRACK_LABELS[tname]} → {int(round(volumes[tname] * 100))} %"
                continue
            if ch == curses.KEY_RIGHT:
                volumes[tname] = max(0.0, min(2.0, volumes.get(tname, 0.0) + 0.05))
                last_msg = f"{TRACK_LABELS[tname]} → {int(round(volumes[tname] * 100))} %"
                continue
            if ch == ord("0"):
                volumes[tname] = 0.0
                last_msg = f"{TRACK_LABELS[tname]} muted"
                continue
            if ch == ord("1"):
                volumes[tname] = 1.0
                last_msg = f"{TRACK_LABELS[tname]} → 100 %"
                continue
            if ch in (ord("r"), ord("R")):
                # Reset to profile defaults (strict 4-stem)
                for trk, val in profile_defaults(profile).items():
                    volumes[trk] = val
                last_msg = "Reset to profile defaults"
                continue

    import curses

    curses.wrapper(ui)

    if not state["confirmed"]:
        raise SystemExit("Mix UI aborted by user.")
    return state["volumes"]


def render_mix(slug: str, profile: str, model: str, volumes: dict, output: Path) -> None:
    """
    Strict 4-stem mixer for Demucs-style output:

        separated/{model}/{slug}/vocals.wav
        separated/{model}/{slug}/bass.wav
        separated/{model}/{slug}/drums.wav
        separated/{model}/{slug}/other.wav

    No virtual guitar/piano stems, no duplication of other.wav.
    """
    separated_dir = BASE_DIR / "separated" / model / slug

    def resolve_stem(track: str) -> Path:
        """
        Resolve one of the 4 canonical stems, or fail loudly if missing.
        """
        p = separated_dir / f"{track}.wav"
        if not p.exists():
            raise SystemExit(f"Stem not found: expected {p}")
        return p

    # ---------------------------------------------
    # Determine ordered track list for this mix
    # ---------------------------------------------
    # Only stems that appear in the volumes dict are used,
    # but we never exceed the canonical 4-stem set.
    ordered_tracks = []
    for t in TRACKS:
        if t in volumes:
            ordered_tracks.append(t)

    if not ordered_tracks:
        raise SystemExit("No valid stems found in volumes config; nothing to mix.")

    # Resolve paths now
    stems = {}
    for t in ordered_tracks:
        stems[t] = resolve_stem(t)

    # Prepare directories
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------
    # Build ffmpeg filtergraph
    # ---------------------------------------------
    filter_parts = []
    labels = []
    inputs = []

    for idx, t in enumerate(ordered_tracks):
        p = stems[t]
        inputs.append(p)

        vol = float(volumes.get(t, 0.0))
        in_label = f"{idx}:a"
        out_label = f"a{idx}"

        # Volume filter per stem
        filter_parts.append(f"[{in_label}]volume={vol:.3f}[{out_label}]")
        labels.append(f"[{out_label}]")

    # amix all active stems
    amix = "".join(labels) + f"amix=inputs={len(ordered_tracks)}:normalize=0[mix]"
    filter_complex = ";".join(filter_parts + [amix])

    # ---------------------------------------------
    # Execute ffmpeg
    # ---------------------------------------------
    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[mix]",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]

    log("FFMPEG", " ".join(cmd), BLUE)
    subprocess.run(cmd, check=True)
    log("MIX", f"Wrote mixed WAV to {output}", GREEN)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Stem mix UI and renderer (strict 4-stem).")

    p.add_argument(
        "--mp3",
        type=str,
        required=True,
        help="Original mp3 path (to derive slug).",
    )
    p.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
    )
    p.add_argument(
        "--model",
        type=str,
        default="htdemucs",
        help="Demucs model name (4-stem).",
    )
    p.add_argument(
        "--mix-ui-only",
        action="store_true",
        help="Only run mix UI and save config.",
    )
    p.add_argument(
        "--render-only",
        action="store_true",
        help="Only render mix WAV from existing config.",
    )
    p.add_argument(
        "--output",
        type=str,
        help="Output WAV path (optional). Defaults to mixes/<slug>_<profile>.wav",
    )

    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    mp3_path = Path(args.mp3).resolve()
    if not mp3_path.exists():
        raise SystemExit(f"mp3 not found: {mp3_path}")
    slug = slugify(mp3_path.stem)

    if args.profile == "lyrics":
        raise SystemExit("Profile 'lyrics' does not require stem mixing; use original mp3.")

    if args.mix_ui_only and args.render_only:
        raise SystemExit("Cannot use --mix-ui-only and --render-only together.")

    out_wav = (
        Path(args.output).resolve()
        if args.output
        else (MIXES_DIR / f"{slug}_{args.profile}.wav")
    )

    # UI-only mode: just capture and save volumes
    if args.mix_ui_only:
        vols = mix_ui(slug, args.profile, args.model)
        log("MIXCFG", f"Saving mix config for slug={slug}, profile={args.profile}", GREEN)
        save_config(slug, args.profile, args.model, vols)
        return

    # render-only or default render
    cfg_vols, cfg_path = load_existing_config(slug, args.profile)
    if cfg_vols is None:
        raise SystemExit(
            f"No mix config found for slug={slug}, profile={args.profile}. "
            f"Run with --mix-ui-only first."
        )

    # Restrict to strict 4-stem keys; ignore any legacy keys
    volumes = {}
    for k in TRACKS:
        if k in cfg_vols:
            try:
                volumes[k] = float(cfg_vols[k])
            except Exception:
                pass

    if not volumes:
        raise SystemExit(
            f"Existing config at {cfg_path} has no usable 4-stem keys; "
            f"delete it or re-run with --mix-ui-only."
        )

    log("MIXCFG", f"Using config from {cfg_path}", GREEN)
    log("MIXCFG", f"Volumes: " + ", ".join(f"{k}={volumes[k]:.2f}" for k in TRACKS), CYAN)

    render_mix(slug, args.profile, args.model, volumes, out_wav)


if __name__ == "__main__":
    main()

# end of 2_stems.py
