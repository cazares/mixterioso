#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

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


TRACKS = ["vocals", "bass", "guitar", "piano", "other"]
TRACK_LABELS = {
    "vocals": "Vocals",
    "bass": "Bass",
    "guitar": "Guitar",
    "piano": "Piano/Keys",
    "other": "Other",
}


def profile_defaults(profile: str) -> dict:
    # volumes are linear multipliers (0.0–2.0)
    if profile == "karaoke":
        return {
            "vocals": 0.0,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }
    if profile == "car-karaoke":
        return {
            "vocals": 0.35,
            "bass": 1.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }
    if profile == "no-bass":
        return {
            "vocals": 1.0,
            "bass": 0.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }
    if profile == "car-bass-karaoke":
        return {
            "vocals": 0.35,
            "bass": 0.0,
            "guitar": 1.0,
            "piano": 1.0,
            "other": 1.0,
        }
    # lyrics or unknown: flat
    return {
        "vocals": 1.0,
        "bass": 1.0,
        "guitar": 1.0,
        "piano": 1.0,
        "other": 1.0,
    }


def load_existing_config(slug: str, profile: str) -> tuple[dict | None, Path | None]:
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    new_path = MIXES_DIR / f"{slug}_{profile}.json"
    old_path = MIXES_DIR / f"{slug}.json"
    path = None
    if new_path.exists():
        path = new_path
    elif old_path.exists():
        path = old_path
    if not path:
        return None, None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        vols = cfg.get("volumes", {})
        if isinstance(vols, dict):
            return vols, path
    except Exception:
        pass
    return None, path


def save_config(slug: str, profile: str, model: str, volumes: dict) -> Path:
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    path = MIXES_DIR / f"{slug}_{profile}.json"
    cfg = {
        "slug": slug,
        "profile": profile,
        "model": model,
        "volumes": volumes,
    }
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    log("MIXCFG", f"Saved mix config to {path}", GREEN)
    return path


def mix_ui(slug: str, profile: str, model: str) -> dict:
    import curses

    defaults = profile_defaults(profile)
    existing_vols, cfg_path = load_existing_config(slug, profile)
    volumes = defaults.copy()

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

            controls = "[UP/DOWN] select  [LEFT/RIGHT] -/+5%  [0] mute  [1] 100%  [r] reset profile  [ENTER/s] save  [q/ESC] abort"
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

            if ch in (27, ord("q"), ord("Q")):
                state["confirmed"] = False
                return

            if ch in (10, 13, ord("s"), ord("S")):
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
    # Expect stems at separated/<model>/<stemname>.wav relative to BASE_DIR
    separated_dir = BASE_DIR / "separated" / model / slug
    stems = {}
    for t in TRACKS:
        p = separated_dir / f"{t}.wav"
        if not p.exists():
            raise SystemExit(f"Stem not found: {p}")
        stems[t] = p

    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    inputs = []
    for t in TRACKS:
        inputs.append(stems[t])

    filter_parts = []
    labels = []
    for idx, t in enumerate(TRACKS):
        vol = float(volumes.get(t, 0.0))
        in_label = f"{idx}:a"
        out_label = f"a{idx}"
        filter_parts.append(f"[{in_label}]volume={vol:.3f}[{out_label}]")
        labels.append(f"[{out_label}]")

    amix = "".join(labels) + f"amix=inputs={len(TRACKS)}:normalize=0[mix]"
    filter_complex = ";".join(filter_parts + [amix])

    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[mix]",
        "-c:a", "pcm_s16le",
        str(output),
    ]

    log("FFMPEG", " ".join(cmd), BLUE)
    subprocess.run(cmd, check=True)
    log("MIX", f"Wrote mixed WAV to {output}", GREEN)



def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Stem mix UI and renderer.")
    p.add_argument("--mp3", type=str, required=True, help="Original mp3 path (to derive slug).")
    p.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
    )
    p.add_argument("--model", type=str, default="htdemucs_6s", help="Demucs model name.")
    p.add_argument("--mix-ui-only", action="store_true", help="Only run mix UI and save config.")
    p.add_argument("--render-only", action="store_true", help="Only render mix WAV from config.")
    p.add_argument("--output", type=str, help="Output WAV path (optional).")
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

    out_wav = Path(args.output).resolve() if args.output else (MIXES_DIR / f"{slug}_{args.profile}.wav")

    if args.mix_ui_only:
        vols = mix_ui(slug, args.profile, args.model)
        save_config(slug, args.profile, args.model, vols)
        return

    # render-only or default render
    cfg_vols, cfg_path = load_existing_config(slug, args.profile)
    if cfg_vols is None:
        raise SystemExit(
            f"No mix config found for slug={slug}, profile={args.profile}. "
            f"Run with --mix-ui-only first."
        )
    log("MIXCFG", f"Using config from {cfg_path}", GREEN)
    render_mix(slug, args.profile, args.model, cfg_vols, out_wav)


if __name__ == "__main__":
    main()

# end of 2_stems.py
