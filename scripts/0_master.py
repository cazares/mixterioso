#!/usr/bin/env python3
# scripts/0_master.py

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# COLORS
# ============================================================================
RESET   = "\033[0m"
BOLD    = "\033[1m"
WHITE   = "\033[97m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"

def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

# ============================================================================
# PATHS
# ============================================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR     = BASE_DIR / "txts"
MP3_DIR     = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
MIXES_DIR   = BASE_DIR / "mixes"
META_DIR    = BASE_DIR / "meta"
OUTPUT_DIR  = BASE_DIR / "output"
UPLOAD_LOG  = BASE_DIR / "uploaded"

# ============================================================================
# Helpers
# ============================================================================
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"

def fmt_secs(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec - m * 60)
    return f"{m:02d}:{s:02d}"

def format_offset_tag(offset: float) -> str:
    sign = "p" if offset >= 0 else "m"
    v = abs(offset)
    sec_int = int(v)
    ms_int  = int(round((v - sec_int) * 1000))
    return f"{sign}{sec_int}p{ms_int:03d}s"

def detect_latest_slug() -> str | None:
    if not META_DIR.exists():
        return None
    files = sorted(
        META_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    return files[0].stem if files else None

def get_meta_title_for_slug(slug: str) -> str:
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return slug.replace("_", " ")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        artist = (meta.get("artist") or "").strip()
        title  = (meta.get("title")  or slug.replace("_", " ")).strip()
        if artist and title:
            return f"{title} by {artist}"
        return title
    except Exception:
        return slug.replace("_", " ")

# ============================================================================
# Step Status — FULLY OFFSET-AWARE
# ============================================================================
def detect_step_status(slug: str, profile: str) -> dict[str, str]:
    status = {"slug": slug, "profile": profile}

    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    meta = META_DIR / f"{slug}.json"
    status["1"] = "DONE" if (mp3.exists() and txt.exists() and meta.exists()) else "MISSING"

    mix = MIXES_DIR / f"{slug}_{profile}.wav"
    status["2"] = "DONE" if mix.exists() else "MISSING"

    csv = TIMINGS_DIR / f"{slug}.csv"
    status["3"] = "DONE" if csv.exists() else "MISSING"

    outputs = list(OUTPUT_DIR.glob(f"{slug}_{profile}_offset_*.mp4"))
    status["4"] = "DONE" if outputs else "MISSING"

    if UPLOAD_LOG.exists() and any(UPLOAD_LOG.glob(f"{slug}_{profile}_offset_*.json")):
        status["5"] = "DONE"
    else:
        status["5"] = "MISSING"

    return status

# ============================================================================
# Utilities
# ============================================================================
def prompt_yes_no(msg: str, default_yes=True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{msg} [{default}]: ").lower().strip()
        if ans == "" and default_yes:
            return True
        if ans == "" and not default_yes:
            return False
        if ans in ("y","yes"): return True
        if ans in ("n","no"):  return False
        print(f"{RED}Please answer Y or N.{RESET}")

def run(cmd: list[str], section: str) -> float:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0

def run_capture(cmd: list[str], section: str) -> tuple[float, str]:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return (time.perf_counter() - t0, cp.stdout)

def read_offset(slug: str) -> float:
    p = OFFSETS_DIR / f"{slug}.txt"
    if not p.exists():
        return 0.0
    try: return float(p.read_text().strip())
    except Exception: return 0.0

def write_offset(slug: str, offset: float) -> None:
    OFFSETS_DIR.mkdir(parents=True, exist_ok=True)
    (OFFSETS_DIR / f"{slug}.txt").write_text(f"{offset:.3f}")

# ============================================================================
# Step 1
# ============================================================================
def run_step1(slug: str, query: str | None, no_ui: bool, extra: list[str]) -> float:
    cmd = [sys.executable, str(SCRIPTS_DIR / "1_txt_mp3.py")]
    cmd += ["--slug", slug]
    if no_ui:
        cmd.append("--no-ui")
    if query:
        for w in query.split():
            cmd.append(w)
    cmd += extra
    return run(cmd, "STEP1")

# ============================================================================
# Step 2
# ============================================================================
def run_step2(
    slug: str,
    profile: str,
    model: str,
    interactive: bool,
    extra: list[str],
    has_levels: bool,
    reset_cache: bool,
) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"

    # Reset cache: remove any existing mix wav immediately
    if reset_cache and mix_wav.exists():
        try:
            mix_wav.unlink()
            log("STEP2", f"reset-cache: removed {mix_wav}", YELLOW)
        except Exception:
            log("STEP2", f"reset-cache: failed to remove {mix_wav}", RED)

    # If no CLI levels at all, skip Demucs/stems entirely and use mp3 directly.
    if not has_levels:
        log(
            "STEP2",
            "No CLI levels provided; skipping Demucs/stems and using original mp3.",
            YELLOW,
        )
        return 0.0

    if interactive:
        use_orig = prompt_yes_no("Use original mp3 (skip Demucs)?", default_yes=False)
    else:
        use_orig = False

    if use_orig:
        MIXES_DIR.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-i", str(mp3), str(mix_wav)]
        cmd += extra
        return run(cmd, "STEP2-BYPASS")

    if profile == "karaoke":
        effective_model = "htdemucs_6s"
        two_stems = False
    else:
        effective_model = model
        two_stems = True

    stems_root = BASE_DIR / "separated" / effective_model
    stems_dir = stems_root / slug

    if reset_cache and stems_dir.exists():
        try:
            for p in stems_dir.glob("*.wav"):
                try:
                    p.unlink()
                except Exception:
                    pass
            log("STEP2", f"reset-cache: cleared stems in {stems_dir}", YELLOW)
        except Exception:
            log("STEP2", f"reset-cache: failed to clear stems in {stems_dir}", RED)

    stems_exist = stems_dir.exists() and any(stems_dir.glob("*.wav"))

    if stems_exist:
        reuse = prompt_yes_no("Stems exist. Reuse?", True) if interactive else True
        if not reuse:
            for p in stems_dir.glob("*.wav"):
                try: p.unlink()
                except Exception: pass
            stems_exist = False

    if not stems_exist:
        cmd = [sys.executable, "-m", "demucs", "-n", effective_model, str(mp3)]
        if two_stems:
            cmd.insert(-1, "--two-stems")
            cmd.insert(-1, "vocals")
        cmd += extra
        run(cmd, "STEP2-DEMUX")

    cmd = [
        sys.executable, str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3", str(mp3),
        "--profile", profile,
        "--model", effective_model,
        "--mix-ui-only",
    ]
    if not interactive:
        cmd.append("--non-interactive")
    cmd += extra
    run(cmd, "STEP2-MIXUI")

    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3", str(mp3),
        "--profile", profile,
        "--model", effective_model,
        "--render-only",
        "--output", str(mix_wav),
    ]
    cmd += extra
    return run(cmd, "STEP2-RENDER")

# ============================================================================
# Step 3
# ============================================================================
def run_step3(slug: str, timing_model_size: str | None = None, extra: list[str] | None = None) -> float:
    if extra is None:
        extra = []
    cmd = [sys.executable, str(SCRIPTS_DIR / "3_auto_timing.py"), "--slug", slug]
    if timing_model_size:
        cmd += ["--model-size", timing_model_size]
    cmd += extra
    return run(cmd, "STEP3")

# ============================================================================
# Step 4 — updated with base/reset-cache/use-cache passthrough
# ============================================================================
def run_step4(
    slug: str,
    profile: str,
    offset: float,
    force: bool = False,
    called_from_master: bool = True,
    extra: list[str] | None = None,
) -> float:
    if extra is None:
        extra = []

    cmd = [
        sys.executable, str(SCRIPTS_DIR / "4_mp4.py"),
        "--slug", slug,
        "--profile", profile,
        "--offset", str(offset),
        "--base", slug,            # NEW
    ]

    # NEW passthrough flags
    if "--reset-cache" in extra:
        cmd.append("--reset-cache")

    if "--use-cache" in extra:
        cmd.append("--use-cache")

    if force:
        cmd.append("--force")

    cmd += extra
    return run(cmd, "STEP4")

# ============================================================================
# Step 5 — upload
# ============================================================================
def run_step5(slug: str, profile: str, offset: float, extra: list[str] | None = None) -> float:
    if extra is None:
        extra = []
    fname = f"{slug}_{profile}_offset_{format_offset_tag(offset)}.mp4"
    path = OUTPUT_DIR / fname
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "5_upload.py"),
        "--file", str(path),
    ]
    cmd += extra
    return run(cmd, "STEP5")

# ============================================================================
# Step selection UI — unchanged
# ============================================================================
def show_pipeline_status(status: dict[str, str]) -> None:
    print()
    print(f"{BOLD}{CYAN}Pipeline status for slug={WHITE}{status['slug']}{CYAN}, "
          f"profile={WHITE}{status['profile']}{RESET}")
    print(f"{WHITE}[1]{RESET} txt+mp3           -> {GREEN if status['1']=='DONE' else RED}{status['1']}{RESET}")
    print(f"{WHITE}[2]{RESET} stems/mix         -> {GREEN if status['2']=='DONE' else RED}{status['2']}{RESET}")
    print(f"{WHITE}[3]{RESET} timings           -> {GREEN if status['3']=='DONE' else RED}{status['3']}{RESET}")
    print(f"{WHITE}[4]{RESET} mp4               -> {GREEN if status['4']=='DONE' else RED}{status['4']}{RESET}")
    print(f"{WHITE}[5]{RESET} upload            -> {GREEN if status['5']=='DONE' else RED}{status['5']}{RESET}")
    print()

def choose_steps_interactive(status: dict[str, str]) -> list[int]:
    if status["1"] == "DONE" and status["2"] == "DONE" and status["3"] == "DONE":
        default = "45"
    elif status["1"] != "DONE":
        default = "1234"
    else:
        default = "234"

    raw = input(
        f"{WHITE}Steps to run "
        f"(1=txt/mp3,2=stems,3=timing,4=mp4,5=upload,0=none){RESET} "
        f"[{MAGENTA}ENTER for {default}{RESET}]: "
    ).strip()

    if not raw:
        raw = default
    if raw == "0":
        return []

    chosen: list[int] = []
    for ch in raw:
        if ch.isdigit():
            i = int(ch)
            if 1 <= i <= 5 and i not in chosen:
                chosen.append(i)
    return chosen

# ============================================================================
# Slug / Query Menu — unchanged
# ============================================================================
def choose_slug_and_query(no_ui: bool):
    latest = detect_latest_slug()

    if no_ui:
        if latest:
            log("SLUG", f'Using latest slug "{latest}" (no-ui mode)', CYAN)
            return latest, None
        return "", None

    print()
    print(f"{BOLD}{CYAN}=== Slug / Query Menu ==={RESET}")

    if latest:
        pretty = get_meta_title_for_slug(latest)
        print(f"{WHITE}1.{RESET} Reuse slug for {GREEN}{pretty}{RESET}")
    else:
        print(f"{WHITE}1.{RESET} {YELLOW}(no previous slug available){RESET}")

    print(f"{WHITE}2.{RESET} Search for a new song")
    print(f"{WHITE}3.{RESET} Pipeline menu")
    print(f"{WHITE}4.{RESET} Exit")

    choice = input(f"{MAGENTA}Choose an option [1-4]: {RESET}").strip()

    if choice == "1":
        if not latest:
            print(f"{RED}No previous slug found.{RESET}")
            return choose_slug_and_query(False)
        pretty = get_meta_title_for_slug(latest)
        log("SLUG", f'Reusing slug "{latest}" ({pretty})', GREEN)
        return latest, None

    if choice == "2":
        query = input(
            f"{WHITE}Enter new search query (e.g. 'nirvana come as you are'): {RESET}"
        ).strip()
        if not query:
            print(f"{RED}Query cannot be empty.{RESET}")
            return choose_slug_and_query(False)
        slug = slugify(query)
        log("SLUG", f'Using slug "{slug}" for new query', GREEN)
        return slug, query

    if choice == "3":
        if latest:
            log("SLUG", f'Using current slug "{latest}"', GREEN)
            return latest, None
        print(f"{YELLOW}No previous slug; search required.{RESET}")
        return choose_slug_and_query(False)

    if choice == "4":
        print(f"{CYAN}Exiting…{RESET}")
        sys.exit(0)

    print(f"{RED}Invalid choice. Please enter 1–4.{RESET}")
    return choose_slug_and_query(False)

# ============================================================================
# ARGS
# ============================================================================
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--slug")
    p.add_argument("--base")
    p.add_argument("--query")
    p.add_argument("--offset", type=float)
    p.add_argument("--model", default="htdemucs")
    p.add_argument("--profile", default="karaoke")
    p.add_argument("--steps")
    p.add_argument("--no-ui", action="store_true")
    p.add_argument("--force-mp4", action="store_true")
    p.add_argument("--no-upload", action="store_true")
    # Level controls
    p.add_argument("--vocals", type=int)
    p.add_argument("--bass", type=int)
    p.add_argument("--drums", type=int)
    p.add_argument("--guitar", type=int)
    # Cache behavior flags
    p.add_argument("--use-cache", action="store_true")
    p.add_argument("--reset-cache", action="store_true")
    p.add_argument(
        "--timing-model-size",
        type=str,
        default=None,
        help="Model size for step 3 auto-timing (tiny/base/small/medium).",
    )
    return p

# ============================================================================
# MAIN
# ============================================================================
def main():
    parser = parse_args()
    args, extra = parser.parse_known_args()
    no_ui = args.no_ui

    slug: str | None = None
    query: str | None = None

    # NEW: base override (minimal diff)
    if args.base:
        slug = slugify(args.base)
        log("SLUG", f'Using base from CLI: "{slug}"', CYAN)

    elif args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using slug from CLI: "{slug}"', CYAN)

    elif args.query:
        raw_q = args.query.strip()
        slug = slugify(raw_q)
        query = raw_q
        log("SLUG", f'Using slug "{slug}" from CLI query', CYAN)

    else:
        slug, query = choose_slug_and_query(no_ui=no_ui)
        if not slug:
            print(f"{RED}No slug provided and no previous slug exists.{RESET}")
            sys.exit(1)

    # offset load/store
    if args.offset is not None:
        offset = args.offset
        write_offset(slug, offset)
        log("OFFSET", f"Using CLI offset={offset:+.3f}s", YELLOW)
    else:
        offset = read_offset(slug)
        log("OFFSET", f"Using stored offset={offset:+.3f}s", CYAN)

    if args.timing_model_size:
        log("TIMING", f"Using timing model size={args.timing_model_size}", CYAN)

    # Determine if any CLI levels were provided
    has_levels = any(
        v is not None
        for v in (args.vocals, args.bass, args.drums, args.guitar)
    )

    status = detect_step_status(slug, args.profile)
    show_pipeline_status(status)

    # Determine steps
    if args.steps:
        steps: list[int] = []
        for ch in args.steps:
            if ch.isdigit():
                i = int(ch)
                if 1 <= i <= 5 and i not in steps:
                    steps.append(i)
        log("MASTER", f"Running requested steps: {steps}", CYAN)
    else:
        if no_ui:
            if status["1"] == "MISSING":
                steps = [1, 2, 3, 4]
            elif status["2"] == "MISSING":
                steps = [2, 3, 4]
            elif status["3"] == "MISSING":
                steps = [3, 4]
            elif status["4"] == "MISSING":
                steps = [4]
            else:
                steps = []
            log("MASTER", f"--no-ui auto-selected steps: {steps}", CYAN)
        else:
            steps = choose_steps_interactive(status)
            log("MASTER", f"Running steps: {steps}", CYAN)

    # Run steps
    t1 = t2 = t3 = t4 = t5 = 0.0

    if 1 in steps:
        t1 = run_step1(slug, query, no_ui, extra)

    if 2 in steps:
        t2 = run_step2(
            slug,
            args.profile,
            args.model,
            interactive=not no_ui,
            extra=extra,
            has_levels=has_levels,
            reset_cache=args.reset_cache,
        )

    if 3 in steps:
        t3 = run_step3(slug, args.timing_model_size, extra=extra)

    if 4 in steps:
        t4 = run_step4(
            slug,
            args.profile,
            offset,
            force=args.force_mp4,
            called_from_master=True,
            extra=extra,
        )

    if 5 in steps and not args.no_upload:
        t5 = run_step5(slug, args.profile, offset, extra=extra)
    elif 5 in steps and args.no_upload:
        log("STEP5", "Upload requested but --no-upload is set; skipping.", YELLOW)

    total = t1 + t2 + t3 + t4 + t5

    if total > 0:
        print()
        print(f"{BOLD}{CYAN}======== PIPELINE SUMMARY ========{RESET}")
        if t1: print(f"{WHITE}Step1 txt/mp3:{RESET}  {GREEN}{fmt_secs(t1)}{RESET}")
        if t2: print(f"{WHITE}Step2 stems:{RESET}    {GREEN}{fmt_secs(t2)}{RESET}")
        if t3: print(f"{WHITE}Step3 timing:{RESET}   {GREEN}{fmt_secs(t3)}{RESET}")
        if t4: print(f"{WHITE}Step4 mp4:{RESET}      {GREEN}{fmt_secs(t4)}{RESET}")
        if t5: print(f"{WHITE}Step5 upload:{RESET}   {GREEN}{fmt_secs(t5)}{RESET}")
        print(f"{GREEN}Total time:{RESET}       {BOLD}{fmt_secs(total)}{RESET}")
        print(f"{BOLD}{CYAN}=================================={RESET}")

if __name__ == "__main__":
    main()

# end of 0_master.py
