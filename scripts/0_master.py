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


BASE_DIR = Path(__file__.resolve()).parent.parent
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
        raise SystemExit("No mp3s found in mp3s/ after 1_txt_mp3.")
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

    step1_done = txt.exists() and mp3.exists() and meta.exists()
    if profile == "lyrics":
        step2_done = True
    else:
        step2_done = mix_wav.exists()
    step3_done = timing_csv.exists()
    step4_done = mp4.exists()

    return {
        1: step1_done,
        2: step2_done,
        3: step3_done,
        4: step4_done,
    }


def print_asset_status(slug: str, profile: str, status: dict) -> None:
    print()
    print(f"{BOLD}Pipeline status for slug={slug}, profile={profile}{RESET}")
    labels = {
        1: "txt+mp3 generation (1_txt_mp3)",
        2: "stems/mix (Demucs + mix UI)",
        3: "timings CSV (3_timing)",
        4: "mp4 generation (4_mp4)",
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


def run_step1_txt_mp3(query: str) -> tuple[str, float]:
    t = run([sys.executable, str(SCRIPTS_DIR / "1_txt_mp3.py"), query], "STEP1")
    slug = detect_slug_from_latest_mp3()
    log("STEP1", f"1_txt_mp3 slug detected: {slug}", GREEN)
    return slug, t


def run_step2_stems(slug: str, profile: str, model: str, interactive: bool) -> float:
    if profile == "lyrics":
        log("STEP2", "Profile 'lyrics' selected, skipping stems/mix.", YELLOW)
        return 0.0

    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    if not mp3_path.exists() or not txt_path.exists():
        raise SystemExit(f"Missing assets for step 2: {mp3_path} or {txt_path}.")

    separated_root = BASE_DIR / "separated"

    # Always prefer 6-stem, then (if absolutely necessary) 4-stem.
    preferred_models: list[str] = []
    if model:
        preferred_models.append(model)
    if "htdemucs_6s" not in preferred_models:
        preferred_models.insert(0, "htdemucs_6s")
    if "htdemucs" not in preferred_models:
        preferred_models.append("htdemucs")  # 4-stem fallback, never 2-stem

    # Check for existing stems we can reuse.
    existing_model = None
    for m in preferred_models:
        d = separated_root / m / slug
        if d.exists():
            existing_model = m
            break

    reuse_stems = False
    actual_model = existing_model

    if existing_model:
        if interactive:
            ans = input(
                f"Stems already exist at {separated_root / existing_model / slug} "
                f"for model '{existing_model}'. Reuse and skip Demucs? [Y/n]: "
            ).strip().lower()
            reuse_stems = ans in ("", "y", "yes")
        else:
            reuse_stems = True

    t_demucs = 0.0

    if not reuse_stems:
        import subprocess as sp

        log("STEP2", f"No reusable stems; trying models {preferred_models}", CYAN)
        actual_model = None
        for m in preferred_models:
            try:
                t_demucs += run(["demucs", "-n", m, str(mp3_path)], "STEP2-DEMUX")
                actual_model = m
                break
            except sp.CalledProcessError:
                log("STEP2", f"Demucs model '{m}' failed, trying next.", YELLOW)
        if actual_model is None:
            raise SystemExit(
                "Demucs failed for 6-stem and 4-stem models; "
                "no 2-stem fallback is allowed by policy."
            )
    elif actual_model is None:
        raise SystemExit("Asked to reuse stems but none were found for any supported model.")

    log("STEP2", f"Using Demucs model '{actual_model}' for mixing.", GREEN)

    # Open mix UI (using remembered percentages) for this model/profile.
    t_mix_ui = run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "2_stems.py"),
            "--mp3",
            str(mp3_path),
            "--profile",
            profile,
            "--model",
            actual_model,
            "--mix-ui-only",
        ],
        "STEP2-MIXUI",
    )

    out_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    t_render = run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "2_stems.py"),
            "--mp3",
            str(mp3_path),
            "--profile",
            profile,
            "--model",
            actual_model,
            "--render-only",
            "--output",
            str(out_wav),
        ],
        "STEP2-RENDER",
    )

    total = t_demucs + t_mix_ui + t_render
    log("STEP2", f"Stems/mix completed in {fmt_secs_mmss(total)}", GREEN)
    return total


def run_step3_timing(slug: str) -> float:
    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    timing_path.parent.mkdir(parents=True, exist_ok=True)

    t = run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "3_timing.py"),
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


def run_step4_mp4(slug: str, profile: str) -> float:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "4_mp4.py"),
        "--slug",
        slug,
        "--profile",
        profile,
    ]
    try:
        t = run(cmd, "STEP4")
        return t
    except subprocess.CalledProcessError as e:
        # 4_mp4 already prints a clear message like:
        # "Audio not found for profile=karaoke: /.../slug_profile.wav"
        # Just add a clean summary here and keep the pipeline alive.
        log(
            "STEP4",
            f"Step 4 failed (exit {e.returncode}). "
            "Most likely the mixed WAV for this slug/profile is missing. "
            "Run step 2 (stems/mix) first.",
            RED,
        )
        return 0.0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Karaoke pipeline master (1=txt/mp3, 2=stems, 3=timing, 4=mp4)."
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--query", type=str, help="Search query for step 1 (1_txt_mp3)")
    src.add_argument("--slug", type=str, help="Slug to operate on (e.g. californication)")
    p.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
    )
    p.add_argument("--model", type=str, default="htdemucs_6s", help="Demucs model name")
    p.add_argument("--steps", type=str, help="Steps to run, e.g. 24 or 1234")
    p.add_argument(
        "--do",
        type=str,
        choices=["new", "remix", "retime", "mp4"],
        help=(
            "High-level action shortcut: "
            "new=1+2+3+4 from query, "
            "remix=2+4 from existing slug, "
            "retime=3+4 from existing slug, "
            "mp4=4 only."
        ),
    )
    p.add_argument("--skip-ui", action="store_true", help="Non-interactive; use --steps / --do as-is")
    return p.parse_args(argv)


def interactive_slug_and_steps(args):
    slug = args.slug
    t1 = 0.0

    # If query is given and no slug, run step 1 first
    if args.query and not slug:
        log("MASTER", f'Running step 1 (1_txt_mp3) for query "{args.query}"', CYAN)
        slug, t1 = run_step1_txt_mp3(args.query)

    # If we still don't have a slug, try to infer last slug from latest mp3
    last_slug = None
    if not slug:
        try:
            mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            if mp3s:
                last_slug = slugify(mp3s[-1].stem)
        except Exception:
            last_slug = None

    if not slug:
        if last_slug:
            prompt = (
                f'Enter search query for step 1 '
                f'(or ENTER to reuse last slug "{last_slug}"): '
            )
        else:
            prompt = "Enter search query for step 1 (or leave blank to use existing slug): "

        try:
            q = input(prompt).strip()
        except EOFError:
            q = ""

        if q:
            log("MASTER", f'Running step 1 (1_txt_mp3) for query "{q}"', CYAN)
            slug, t1 = run_step1_txt_mp3(q)
        else:
            if last_slug:
                slug = last_slug
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
            q = input("Step 1 selected. Enter search query for 1_txt_mp3: ").strip()
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
        slug, _ = run_step1_txt_mp3(args.query)

    if not slug:
        raise SystemExit("Slug is required for steps 2–4 (use --slug or include step 1 with --query).")

    slug = slugify(slug)
    return slug, steps


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    total_start = time.perf_counter()

    # High-level shortcuts: --do
    if args.do:
        if args.do == "new":
            if not args.query:
                raise SystemExit("--do new requires --query.")
            args.steps = "1234"
        elif args.do == "remix":
            if not args.slug:
                raise SystemExit("--do remix requires --slug.")
            args.steps = "24"
        elif args.do == "retime":
            if not args.slug:
                raise SystemExit("--do retime requires --slug.")
            args.steps = "34"
        elif args.do == "mp4":
            if not args.slug:
                raise SystemExit("--do mp4 requires --slug.")
            args.steps = "4"

        # When using --do, run non-interactively by default.
        args.skip_ui = True

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
        slug, t1 = run_step1_txt_mp3(args.query)

    if 2 in steps:
        t2 = run_step2_stems(slug, args.profile, args.model, interactive=not args.skip_ui)

    if 3 in steps:
        t3 = run_step3_timing(slug)

    if 4 in steps:
        t4 = run_step4_mp4(slug, args.profile)

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

# end of 0_master.py
