#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
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
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def fmt_secs_mmss(sec: float) -> str:
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{sec:6.2f} s  ({m:02d}:{s:02d})"


def run(cmd: list[str], section: str) -> float:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    return t1 - t0


def detect_slug_from_latest_mp3() -> str:
    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found in mp3s/ after gen_txt_mp3.")
    return slugify(mp3s[-1].stem)


def load_meta(slug: str):
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return None, None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data.get("artist"), data.get("title")
    except Exception:
        return None, None


def detect_assets(slug: str, profile: str) -> dict:
    txt = TXT_DIR / f"{slug}.txt"
    mp3 = MP3_DIR / f"{slug}.mp3"
    meta = META_DIR / f"{slug}.json"
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    timing_csv = TIMINGS_DIR / f"{slug}.csv"
    mp4 = OUTPUT_DIR / f"{slug}_{profile}.mp4"

    return {
        1: txt.exists() and mp3.exists() and meta.exists(),
        2: mix_wav.exists(),
        3: timing_csv.exists(),
        4: mp4.exists(),
    }


def print_asset_status(slug: str, profile: str, status: dict) -> None:
    print()
    print(f"{BOLD}Pipeline status for slug={slug}, profile={profile}{RESET}")
    labels = {
        1: "txt+mp3 generation (gen_txt_mp3)",
        2: "stems/mix (demucs + gen_stems)",
        3: "timings CSV (gen_timing)",
        4: "mp4 generation (gen_mp4)",
    }
    for step in range(1, 5):
        s = "DONE" if status.get(step) else "MISSING"
        color = GREEN if status.get(step) else YELLOW
        print(f"{color}[{step}] {labels[step]}  -> {s}{RESET}")
    print()


def suggest_steps(status: dict) -> str:
    missing = "".join(str(k) for k in range(1, 5) if not status.get(k))
    return missing or "0"


def parse_steps_string(s: str) -> list[int]:
    steps: list[int] = []
    for ch in s:
        if ch in "01234":
            v = int(ch)
            if v not in steps:
                steps.append(v)
    steps.sort()
    return steps


def run_step1_gen_txt_mp3(query: str) -> tuple[str, float]:
    t = run([sys.executable, str(SCRIPTS_DIR / "gen_txt_mp3.py"), query], "STEP1")
    slug = detect_slug_from_latest_mp3()
    log("STEP1", f"gen_txt_mp3 slug detected: {slug}", GREEN)
    return slug, t


def run_step2_gen_stems(slug: str, profile: str, model: str) -> float:
    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    if not mp3_path.exists() or not txt_path.exists():
        raise SystemExit(f"Missing assets for step 2: {mp3_path} or {txt_path}.")

    # 2a: Demucs separation
    t_demucs = run(["demucs", "-n", model, str(mp3_path)], "STEP2-DEMUX")

    # 2b: Mix UI (writes mixes/<slug>.json)
    t_mix_ui = run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "gen_stems.py"),
            "--txt",
            str(txt_path),
            "--mp3",
            str(mp3_path),
            "--profile",
            profile,
            "--mix-ui-only",
        ],
        "STEP2-MIXUI",
    )

    # 2c: Render mix WAV
    mix_cfg = MIXES_DIR / f"{slug}.json"
    out_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    t_render = run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "gen_stems.py"),
            "--mp3",
            str(mp3_path),
            "--profile",
            profile,
            "--mix-config",
            str(mix_cfg),
            "--render-only",
            "--reuse-stems",
            "--model",
            model,
            "--output",
            str(out_wav),
        ],
        "STEP2-RENDER",
    )

    total = t_demucs + t_mix_ui + t_render
    log("STEP2", f"Stems/mix completed in {fmt_secs_mmss(total)}", GREEN)
    return total


def run_step3_gen_timing(slug: str) -> float:
    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    timing_path.parent.mkdir(parents=True, exist_ok=True)

    t = run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "gen_timing.py"),
            "--txt",
            str(txt_path),
            "--audio",
            str(mp3_path),
            "--timings",
            str(timing_path),
        ],
        "STEP3",
    )
    return t


def run_step4_gen_mp4(slug: str, profile: str) -> float:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "gen_mp4.py"),
        "--slug",
        slug,
        "--profile",
        profile,
    ]
    t = run(cmd, "STEP4")
    return t


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Karaoke pipeline master (1=txt/mp3, 2=stems, 3=timing, 4=mp4)."
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--query", type=str, help="Search query for step 1 (gen_txt_mp3)")
    src.add_argument("--slug", type=str, help="Slug to operate on (e.g. californication)")
    p.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
    )
    p.add_argument("--model", type=str, default="htdemucs_6s", help="Demucs model name")
    p.add_argument("--steps", type=str, help="Steps to run, e.g. 24 or 1234")
    p.add_argument("--skip-ui", action="store_true", help="Non-interactive; use --steps as-is")
    return p.parse_args(argv)


def interactive_slug_and_steps(args):
    slug = args.slug
    t1 = 0.0

    if args.query and not slug:
        log("MASTER", f'Running step 1 gen_txt_mp3 for query "{args.query}"', CYAN)
        slug, t1 = run_step1_gen_txt_mp3(args.query)

    if not slug:
        try:
            q = input("Enter search query for step 1 (or leave blank to use existing slug): ").strip()
        except EOFError:
            q = ""
        if q:
            log("MASTER", f'Running step 1 gen_txt_mp3 for query "{q}"', CYAN)
            slug, t1 = run_step1_gen_txt_mp3(q)
        else:
            try:
                slug = input("Enter existing slug (e.g. californication): ").strip()
            except EOFError:
                slug = ""
            if not slug:
                raise SystemExit("Slug is required if no query is given.")

    slug = slugify(slug)
    status = detect_assets(slug, args.profile)
    print_asset_status(slug, args.profile, status)

    suggested = suggest_steps(status)
    try:
        s = input(
            f"Steps to run (1=txt/mp3,2=stems,3=timing,4=mp4, 0=none, ENTER for suggested={suggested}): "
        ).strip()
    except EOFError:
        s = ""
    if not s:
        s = suggested
    if s == "0":
        log("MASTER", "Nothing selected; exiting.", YELLOW)
        return slug, [], t1

    steps = parse_steps_string(s)
    if 1 in steps and not args.query:
        try:
            q = input("Step 1 selected. Enter search query for gen_txt_mp3: ").strip()
        except EOFError:
            q = ""
        if not q:
            log("MASTER", "No query provided; dropping step 1.", YELLOW)
            steps.remove(1)
        else:
            args.query = q

    return slug, steps, t1


def noninteractive_slug_and_steps(args):
    if not args.steps:
        raise SystemExit("--skip-ui requires --steps like 24 or 1234.")
    steps = parse_steps_string(args.steps)
    if not steps:
        return "", []

    slug = args.slug
    if 1 in steps:
        if not args.query:
            raise SystemExit("Step 1 selected but no --query given.")
        slug, _ = run_step1_gen_txt_mp3(args.query)

    if not slug:
        raise SystemExit("Slug is required for steps 2–4 (use --slug or include step 1 with --query).")

    slug = slugify(slug)
    return slug, steps


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    total_start = time.perf_counter()

    if args.skip_ui:
        slug, steps = noninteractive_slug_and_steps(args)
        t1 = 0.0
    else:
        slug, steps, t1 = interactive_slug_and_steps(args)

    if not steps:
        return

    log("MASTER", f"Running steps {steps} for slug={slug}, profile={args.profile}", CYAN)

    t2 = t3 = t4 = 0.0

    if 1 in steps and not args.skip_ui and t1 == 0.0:
        if not args.query:
            raise SystemExit("Interactive step 1 requires a query.")
        slug, t1 = run_step1_gen_txt_mp3(args.query)

    if 2 in steps:
        t2 = run_step2_gen_stems(slug, args.profile, args.model)

    if 3 in steps:
        t3 = run_step3_gen_timing(slug)

    if 4 in steps:
        t4 = run_step4_gen_mp4(slug, args.profile)

    total_end = time.perf_counter()
    total = total_end - total_start

    print()
    print(f"{BOLD}{BLUE}========= PIPELINE SUMMARY ({slug}, profile={args.profile}) ========={RESET}")
    print(f"{CYAN}Step 1 txt/mp3:  {fmt_secs_mmss(t1)}{RESET}")
    print(f"{CYAN}Step 2 stems:    {fmt_secs_mmss(t2)}{RESET}")
    print(f"{CYAN}Step 3 timing:   {fmt_secs_mmss(t3)}{RESET}")
    print(f"{CYAN}Step 4 mp4:      {fmt_secs_mmss(t4)}{RESET}")
    print(f"{BOLD}{GREEN}Total pipeline: {fmt_secs_mmss(total)}{RESET}")
    print(f"{BOLD}{BLUE}====================================================={RESET}")


if __name__ == "__main__":
    main()

# end of gen_master.py
