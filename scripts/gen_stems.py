#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
MIXES_DIR = BASE_DIR / "mixes"
SEPARATED_DIR = BASE_DIR / "separated"


def slugify(text: str) -> str:
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def infer_slug(txt_path, mp3_path):
    if txt_path is not None:
        return slugify(txt_path.stem)
    if mp3_path is not None:
        return slugify(mp3_path.stem)
    raise ValueError("Need at least txt or mp3 path to infer slug")


def profile_base_levels(profile: str) -> dict:
    levels = dict(vocals=100, bass=100, guitar=100, piano=100, other=100, master=100)
    if profile == "lyrics":
        return levels
    if profile == "karaoke":
        levels["vocals"] = 0
        return levels
    if profile == "car-karaoke":
        levels["vocals"] = 30
        return levels
    if profile == "no-bass":
        levels["bass"] = 0
        return levels
    if profile == "car-bass-karaoke":
        levels["vocals"] = 30
        levels["bass"] = 0
        return levels
    return levels


def apply_cli_overrides(levels: dict, args) -> dict:
    for stem in ["vocals", "bass", "guitar", "piano", "other", "master"]:
        val = getattr(args, stem, None)
        if val is not None:
            levels[stem] = int(val)
    return levels


def needs_demucs(levels: dict, profile: str) -> bool:
    if profile == "lyrics":
        for stem in ["vocals", "bass", "guitar", "piano", "other"]:
            if levels[stem] != 100:
                return True
        return False
    for stem in ["vocals", "bass", "guitar", "piano", "other"]:
        if levels[stem] != 100:
            return True
    return False


def stems_dir_for(slug: str, model: str) -> Path:
    return SEPARATED_DIR / model / slug


def stems_exist(slug: str, model: str) -> bool:
    d = stems_dir_for(slug, model)
    if not d.exists():
        return False
    expected = ["vocals.wav", "bass.wav", "guitar.wav", "piano.wav", "drums.wav", "other.wav"]
    return all((d / name).exists() for name in expected)


def run_demucs(mp3_path: Path, slug: str, model: str, reuse_stems: bool, force_demucs: bool, interactive: bool) -> None:
    if stems_exist(slug, model):
        if force_demucs:
            log("DEMUX", f"Stems already exist for {slug} but --force-demucs set; rerunning.", YELLOW)
        elif reuse_stems and not interactive:
            log("DEMUX", f"Reusing existing stems for {slug} (model={model}).", GREEN)
            return
        elif interactive:
            print(f"{YELLOW}[WARN]{RESET} Stems already exist at {stems_dir_for(slug, model)}")
            ans = input("Reuse existing stems instead of rerunning Demucs? [Y/n]: ").strip().lower()
            if ans in ("", "y", "yes"):
                log("DEMUX", f"Reusing existing stems for {slug} (model={model}).", GREEN)
                return
            log("DEMUX", f"Rerunning Demucs for {slug}.", YELLOW)
        else:
            log("DEMUX", f"Reusing existing stems for {slug} (model={model}).", GREEN)
            return
    cmd = [
        "demucs",
        "-n",
        model,
        str(mp3_path),
    ]
    log("DEMUX", f"Running Demucs: {' '.join(cmd)}", MAGENTA)
    subprocess.run(cmd, check=True)


def save_mix_config(slug: str, levels: dict, profile: str) -> Path:
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    path = MIXES_DIR / f"{slug}.json"
    data = {"profile": profile, "levels": levels}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log("MIX", f"Saved mix config to {path}", GREEN)
    return path


def load_mix_config(path: Path) -> tuple:
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = data.get("profile", "karaoke")
    levels = data.get("levels") or {}
    return profile, levels


def render_mix(slug: str, profile: str, levels: dict, model: str, output: Path) -> None:
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    stems_dir = stems_dir_for(slug, model)
    if not stems_dir.exists():
        raise SystemExit(f"Error: stems directory {stems_dir} does not exist. Run Demucs first.")

    inputs = [
        ("vocals", stems_dir / "vocals.wav", levels["vocals"]),
        ("bass", stems_dir / "bass.wav", levels["bass"]),
        ("guitar", stems_dir / "guitar.wav", levels["guitar"]),
        ("piano", stems_dir / "piano.wav", levels["piano"]),
        ("drums", stems_dir / "drums.wav", levels["other"]),
        ("other", stems_dir / "other.wav", levels["other"]),
    ]

    for name, path, _ in inputs:
        if not path.exists():
            raise SystemExit(f"Error: missing stem {name} at {path}")

    filter_parts = []
    labels = []
    for idx, (_, _, vol) in enumerate(inputs):
        scale = max(vol, 0) / 100.0
        label = f"s{idx}"
        filter_parts.append(f"[{idx}:a]volume={scale:.3f}[{label}]")
        labels.append(label)

    amix_inputs = "".join(f"[{lab}]" for lab in labels)
    filter_parts.append(f"{amix_inputs}amix=inputs={len(labels)}:normalize=0[mix]")
    master_scale = max(levels["master"], 0) / 100.0
    filter_parts.append(f"[mix]volume={master_scale:.3f}[out]")
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"]
    for _, path, _ in inputs:
        cmd.extend(["-i", str(path)])
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    log("FFMPEG", f"Rendering mix to {output}", CYAN)
    subprocess.run(cmd, check=True)


def copy_original_to_mix(mp3_path: Path, output: Path) -> None:
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp3_path),
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    log("FFMPEG", f"Copying original audio to {output}", CYAN)
    subprocess.run(cmd, check=True)


def run_mix_ui(slug: str, profile: str, levels: dict) -> dict:
    import curses

    def clamp(x: int) -> int:
        return max(0, min(150, x))

    def ui(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_GREEN, -1)

        stems = ["vocals", "bass", "guitar", "piano", "other", "master"]
        labels = {
            "vocals": "VOCALS",
            "bass": "BASS",
            "guitar": "GUITAR",
            "piano": "PIANO",
            "other": "OTHER+DRUMS",
            "master": "MASTER",
        }
        idx = 0

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            title = f"[MIX] {slug}  profile={profile}"
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(0, 0, title[: w - 1])
            stdscr.attroff(curses.color_pair(4))
            stdscr.addstr(1, 0, "Use ↑/↓ to select, ←/→ or +/- to change, R reset, ENTER to save, Q to cancel")

            for row, stem in enumerate(stems, start=3):
                val = levels[stem]
                bar_len = int(val / 5)
                bar = "#" * bar_len + "." * (30 - bar_len)
                label = labels[stem]
                line = f"{label:12} {val:3d}%  {bar}"
                if row < h:
                    if row == 3 + idx:
                        stdscr.attron(curses.color_pair(1))
                        stdscr.addstr(row, 0, line[: w - 1])
                        stdscr.attroff(curses.color_pair(1))
                    else:
                        if val == 0:
                            stdscr.attron(curses.color_pair(2))
                        elif val > 100:
                            stdscr.attron(curses.color_pair(3))
                        else:
                            stdscr.attron(curses.color_pair(4))
                        stdscr.addstr(row, 0, line[: w - 1])
                        stdscr.attroff(curses.color_pair(4))
                        stdscr.attroff(curses.color_pair(3))
                        stdscr.attroff(curses.color_pair(2))

            footer = "[SPACE/ENTER] save  [↑/↓] select  [←/→ +/-] change  [R] reset  [Q] cancel"
            stdscr.addstr(h - 1, 0, footer[: w - 1])

            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                raise SystemExit("Mix UI cancelled by user.")
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(stems)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(stems)
            elif ch in (curses.KEY_LEFT, ord("h"), ord("-")):
                stem = stems[idx]
                levels[stem] = clamp(levels[stem] - 5)
            elif ch in (curses.KEY_RIGHT, ord("l"), ord("+"), ord("=")):
                stem = stems[idx]
                levels[stem] = clamp(levels[stem] + 5)
            elif ch in (ord("r"), ord("R")):
                base = profile_base_levels(profile)
                levels.update(base)
            elif ch in (ord(" "), curses.KEY_ENTER, 10, 13):
                break

    curses.wrapper(ui)
    return levels


def parse_args(argv):
    p = argparse.ArgumentParser(description="Tracking: Demucs + stem mix.")
    p.add_argument("--txt", type=str, help="Lyrics txt path")
    p.add_argument("--mp3", type=str, required=True, help="Source mp3 path")
    p.add_argument("--profile", type=str, default="karaoke",
                   choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"])
    p.add_argument("--vocals", type=int)
    p.add_argument("--bass", type=int)
    p.add_argument("--guitar", type=int)
    p.add_argument("--piano", type=int)
    p.add_argument("--other", type=int)
    p.add_argument("--master", type=int)
    p.add_argument("--mix-ui-only", action="store_true", help="Only run mix UI and save JSON config.")
    p.add_argument("--render-only", action="store_true", help="Only render mix from stems + config.")
    p.add_argument("--mix-config", type=str, help="Path to mix JSON config.")
    p.add_argument("--model", type=str, default="htdemucs_6s", help="Demucs model name.")
    p.add_argument("--reuse-stems", action="store_true", help="Reuse existing stems without prompting.")
    p.add_argument("--force-demucs", action="store_true", help="Force rerun Demucs even if stems exist.")
    p.add_argument("--output", type=str, help="Output audio path (wav).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    mp3_path = Path(args.mp3).resolve()
    if not mp3_path.exists():
        raise SystemExit(f"Error: mp3 file not found: {mp3_path}")
    txt_path = Path(args.txt).resolve() if args.txt else None

    slug = infer_slug(txt_path, mp3_path)
    profile = args.profile

    # Starting levels
    if args.mix_config:
        cfg_profile, cfg_levels = load_mix_config(Path(args.mix_config))
        if not args.profile:
            profile = cfg_profile
        levels = profile_base_levels(profile)
        levels.update(cfg_levels)
    else:
        levels = profile_base_levels(profile)

    levels = apply_cli_overrides(levels, args)

    if args.mix_ui_only and args.render_only:
        raise SystemExit("Error: --mix-ui-only and --render-only are mutually exclusive.")

    # Mix UI only
    if args.mix_ui_only:
        log("MODE", f"Mix UI only for slug={slug}, profile={profile}", BOLD)
        levels = run_mix_ui(slug, profile, levels)
        save_mix_config(slug, levels, profile)
        return

    # Determine output path
    if args.output:
        output = Path(args.output).resolve()
    else:
        MIXES_DIR.mkdir(parents=True, exist_ok=True)
        output = MIXES_DIR / f"{slug}_{profile}.wav"

    need_demucs = needs_demucs(levels, profile)

    # Render-only (used by orchestrator)
    if args.render_only:
        log("MODE", f"Render-only for slug={slug}, profile={profile}", BOLD)
        if not need_demucs:
            copy_original_to_mix(mp3_path, output)
        else:
            run_demucs(mp3_path, slug, args.model, args.reuse_stems, args.force_demucs, interactive=False)
            render_mix(slug, profile, levels, args.model, output)
        log("DONE", f"Rendered mix to {output}", GREEN)
        print(f"Suggested timing command:\n  python3 scripts/timing_editor.py --txt txts/{slug}.txt --audio {output}")
        return

    # Full interactive: mix UI + Demucs + render
    log("MODE", f"Full tracking for slug={slug}, profile={profile}", BOLD)
    levels = run_mix_ui(slug, profile, levels)
    save_mix_config(slug, levels, profile)

    if not need_demucs:
        copy_original_to_mix(mp3_path, output)
    else:
        run_demucs(mp3_path, slug, args.model, args.reuse_stems, args.force_demucs, interactive=True)
        render_mix(slug, profile, levels, args.model, output)
    log("DONE", f"Rendered mix to {output}", GREEN)
    print(f"Suggested timing command:\n  python3 scripts/timing_editor.py --txt txts/{slug}.txt --audio {output}")


if __name__ == "__main__":
    main()

# end of gen_stems.py