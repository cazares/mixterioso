#!/usr/bin/env python3
# scripts/0_master.py

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# ----- COLORS -----
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"   # bright white — excellent on black backgrounds


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
MIXES_DIR = BASE_DIR / "mixes"
META_DIR = BASE_DIR / "meta"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_LOG = BASE_DIR / "uploaded"


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
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
    """
    Convert numeric offset (seconds) into tag like:
      +0.000 -> p0p000s
      +1.500 -> p1p500s
      -0.500 -> m0p500s
    """
    sign = "p" if offset >= 0 else "m"
    val = abs(offset)
    sec_int = int(val)
    ms_int = int(round((val - sec_int) * 1000))
    return f"{sign}{sec_int}p{ms_int:03d}s"


def detect_latest_slug() -> str | None:
    if not META_DIR.exists():
        return None
    files = sorted(META_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return files[0].stem if files else None


def load_meta_fields(slug: str) -> tuple[str | None, str | None]:
    """
    Returns (title, artist) or (None, None).
    """
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return None, None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        title = (data.get("title") or "").strip() or None
        artist = (data.get("artist") or "").strip() or None
        return title, artist
    except:
        return None, None


def detect_step_status(slug: str, profile: str) -> dict[str, str]:
    status = {"slug": slug, "profile": profile}

    # Step 1: txt/mp3/meta
    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    meta = META_DIR / f"{slug}.json"
    status["1"] = "DONE" if (mp3.exists() and txt.exists() and meta.exists()) else "MISSING"

    # Step 2: mix wav
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    status["2"] = "DONE" if mix_wav.exists() else "MISSING"

    # Step 3: timings
    csv = TIMINGS_DIR / f"{slug}.csv"
    status["3"] = "DONE" if csv.exists() else "MISSING"

    # Step 4: mp4
    mp4s = list(OUTPUT_DIR.glob(f"{slug}_{profile}_offset_*.mp4"))
    status["4"] = "DONE" if mp4s else "MISSING"

    # Step 5: upload
    if UPLOAD_LOG.exists() and any(UPLOAD_LOG.glob(f"{slug}_{profile}_offset_*.json")):
        status["5"] = "DONE"
    else:
        status["5"] = "MISSING"

    return status


def prompt_yes_no(msg: str, default_yes=True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{WHITE}{msg}{RESET} [{default}]: ").strip().lower()
        if not ans:
            return default_yes
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print(f"{YELLOW}Please answer y or n.{RESET}")


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
    path = OFFSETS_DIR / f"{slug}.txt"
    if not path.exists():
        return 0.0
    try:
        return float(path.read_text().strip())
    except:
        return 0.0


def write_offset(slug: str, offset: float) -> None:
    OFFSETS_DIR.mkdir(parents=True, exist_ok=True)
    (OFFSETS_DIR / f"{slug}.txt").write_text(f"{offset:.3f}")


# ---------------------------------------------------------
# Step 1
# ---------------------------------------------------------
def run_step1(slug: str, query: str | None, no_ui: bool) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    meta = META_DIR / f"{slug}.json"

    if mp3.exists() and txt.exists() and meta.exists():
        log("STEP1", "Already have txt/mp3/meta — skipping.", GREEN)
        return 0.0

    # Build command
    cmd = [sys.executable, str(SCRIPTS_DIR / "1_txt_mp3.py"), "--slug", slug]

    # Add --no-ui if master is running in no-ui mode
    if no_ui:
        cmd.append("--no-ui")

    # Append query *as positional arguments* (not --query)
    if query:
        cmd += query.split()   # ← correct expected format

    return run(cmd, "STEP1")
# ---------------------------------------------------------
# Step 2  (Demucs → mix UI → render)
# ---------------------------------------------------------
def run_step2(slug: str, profile: str, model: str, interactive: bool, no_ui: bool) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"

    # If UI disabled globally:
    if no_ui:
        interactive = False

    # Ask user about bypassing separation
    if interactive:
        bypass = prompt_yes_no("Use original mix (100% all tracks, skip Demucs)?", False)
    else:
        bypass = False

    if bypass:
        MIXES_DIR.mkdir(parents=True, exist_ok=True)
        if mix_wav.exists():
            log("STEP2", f"Bypass: mix already exists at {mix_wav}", GREEN)
            return 0.0
        cmd = ["ffmpeg", "-y", "-i", str(mp3), str(mix_wav)]
        return run(cmd, "STEP2-BYPASS")

    # Demucs model rules
    if profile == "karaoke":
        effective_model = "htdemucs_6s"
        two_stems = False
    else:
        effective_model = model
        two_stems = True

    stems_root = BASE_DIR / "separated" / effective_model
    stems_dir = stems_root / slug
    stems_exist = stems_dir.exists() and any(stems_dir.glob("*.wav"))

    if stems_exist and interactive:
        reuse = prompt_yes_no("Stems exist. Reuse?", True)
        if not reuse:
            for p in stems_dir.glob("*.wav"):
                try:
                    p.unlink()
                except:
                    pass
            stems_exist = False

    # If stems missing, run Demucs
    if not stems_exist:
        cmd = [sys.executable, "-m", "demucs", "-n", effective_model, str(mp3)]
        if two_stems:
            cmd.insert(-1, "--two-stems")
            cmd.insert(-1, "vocals")
            section = "STEP2-2STEM"
        else:
            section = "STEP2-6STEM"
        t_sep = run(cmd, section)
    else:
        log("STEP2", "Reusing existing stems.", GREEN)
        t_sep = 0.0

    # Mix UI
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3", str(mp3),
        "--profile", profile,
        "--model", effective_model,
        "--mix-ui-only",
    ]
    if not interactive:
        cmd.append("--non-interactive")
    t_ui = run(cmd, "STEP2-MIXUI")

    # Render WAV
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3", str(mp3),
        "--profile", profile,
        "--model", effective_model,
        "--render-only",
        "--output", str(mix_wav),
    ]
    t_render = run(cmd, "STEP2-RENDER")

    return t_sep + t_ui + t_render


# ---------------------------------------------------------
# Step 3  (auto-timing or manual timing)
# ---------------------------------------------------------
def run_step3(slug: str, no_ui: bool) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    auto_script = SCRIPTS_DIR / "3_auto_timing.py"

    # auto timing
    if auto_script.exists():
        cmd = [
            sys.executable,
            str(auto_script),
            "--slug", slug,
            "--mp3", str(mp3),
            "--txt", str(txt),
        ]
        if no_ui:
            cmd.append("--no-ui")
        return run(cmd, "STEP3-AUTO")

    # manual timing
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "3_timing.py"),
        "--txt", str(txt),
        "--audio", str(mp3),
        "--timings", str(TIMINGS_DIR / f"{slug}.csv"),
    ]
    if no_ui:
        cmd.append("--no-ui")
    return run(cmd, "STEP3")


# ---------------------------------------------------------
# Step 4  (mp4 rendering)
# ---------------------------------------------------------
def run_step4(slug: str, profile: str, offset: float, force: bool, no_ui: bool) -> float:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "4_mp4.py"),
        "--slug", slug,
        "--profile", profile,
        "--offset", str(offset),
        "--no-post-ui",    # master handles uploads
    ]
    if force:
        cmd.append("--force")
    if no_ui:
        cmd.append("--no-ui")

    return run(cmd, "STEP4")


# ---------------------------------------------------------
# Step 5  (YouTube upload)
# ---------------------------------------------------------
def run_step5(slug: str, profile: str, offset: float, no_ui: bool) -> float:
    if UPLOAD_LOG.exists() and any(UPLOAD_LOG.glob(f"{slug}_{profile}_offset_*.json")):
        log("STEP5", "Upload already recorded in uploaded/; skipping.", GREEN)
        return 0.0

    tag = format_offset_tag(offset)
    mp4_path = OUTPUT_DIR / f"{slug}_{profile}_offset_{tag}.mp4"

    if not mp4_path.exists():
        candidates = sorted(
            OUTPUT_DIR.glob(f"{slug}_{profile}_offset_*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise SystemExit(
                f"{RED}No MP4 found for upload for slug={slug}, profile={profile}, "
                f"offset={offset:+.3f}s{RESET}"
            )
        mp4_path = candidates[0]
        log("STEP5", f"No exact-offset MP4; using latest {mp4_path.name}", YELLOW)
    else:
        log("STEP5", f"Using MP4 {mp4_path.name} for upload", CYAN)

    # --------------------------
    # Title construction
    # --------------------------
    meta_path = META_DIR / f"{slug}.json"
    artist = ""
    title = slug.replace("_", " ")
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            artist = (data.get("artist") or "").strip()
            title = (data.get("title") or title).strip()
        except:
            pass

    base = f"{artist} - {title}".strip(" -")
    base_space = base + " " if base else ""

    if not no_ui:
        suffix = input(
            f'{WHITE}Additional title text to append to "{base_space}" '
            f'(e.g. "(35% Vocals)") [ENTER for none]: {RESET}'
        ).strip()
    else:
        suffix = ""

    final_title = (base_space + suffix).strip() if suffix else base
    if not final_title:
        final_title = mp4_path.stem

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "5_upload.py"),
        "--file", str(mp4_path),
        "--slug", slug,
        "--profile", profile,
        "--offset", str(offset),
        "--title", final_title,
        "--privacy", "private",
    ]
    if no_ui:
        cmd.append("--no-ui")

    return run(cmd, "STEP5")
# ---------------------------------------------------------
# Parse CLI arguments
# ---------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Master orchestrator for Karaoke Time pipeline.")

    p.add_argument("--slug", type=str)
    p.add_argument("--query", type=str)
    p.add_argument("--profile", type=str, default="karaoke")
    p.add_argument("--model", type=str, default="htdemucs")
    p.add_argument("--offset", type=float, default=None)
    p.add_argument("--steps", type=str, help="E.g. 12345 or 45")

    # Hybrid UI flag
    p.add_argument("--no-ui", action="store_true",
                   help="Disable all interactive prompts in master + sub-steps.")

    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--force-mp4", action="store_true")

    return p.parse_args()


# ---------------------------------------------------------
# Slug/Query Menu
# ---------------------------------------------------------
def slug_selection_menu(args) -> tuple[str, str | None]:
    """
    Runs only when user provided NO --slug and NO --query.
    Offers:
      1. Reuse previous slug "foo" (Title by Artist) with confirmation
      2. Search for a new song
      3. Pipeline menu  (jump to step selection screen)
      4. Exit
    """
    while True:
        last_slug = detect_latest_slug()

        print()
        print(f"{BOLD}{CYAN}=== Slug / Query Menu ==={RESET}")

        # Build option 1 text
        if last_slug:
            title, artist = load_meta_fields(last_slug)
            if title and artist:
                opt1 = f'Reuse slug for {WHITE}{title}{RESET} by {WHITE}{artist}{RESET}'
            else:
                opt1 = f'Reuse slug "{WHITE}{last_slug}{RESET}"'
        else:
            opt1 = None

        # Print menu
        if opt1:
            print(f"{WHITE}1.{RESET} {opt1}")
        else:
            print(f"{WHITE}1.{RESET} (No previous slug available)")

        print(f"{WHITE}2.{RESET} Search for a new song")
        print(f"{WHITE}3.{RESET} Pipeline menu")
        print(f"{WHITE}4.{RESET} Exit")

        choice = input(f"{MAGENTA}Choose an option [1-4]: {RESET}").strip()

        # 1) Reuse previous slug
        if choice == "1":
            if not last_slug:
                print(f"{YELLOW}No previous slug available.{RESET}")
                continue

            confirm = prompt_yes_no(
                f'Proceed with slug "{last_slug}"?',
                default_yes=True
            )
            if confirm:
                return last_slug, None
            else:
                continue  # redisplay menu

        # 2) Enter new query
        elif choice == "2":
            raw = input(
                f"{WHITE}Enter new search query (e.g. 'nirvana come as you are'): {RESET}"
            ).strip()
            if not raw:
                print(f"{YELLOW}Empty query not allowed.{RESET}")
                continue
            return slugify(raw), raw

        # 3) Go directly to pipeline menu (no change to slug)
        elif choice == "3":
            if not last_slug:
                print(f"{YELLOW}No previous slug found; cannot enter pipeline menu.{RESET}")
                continue
            return last_slug, "__PIPELINE_ONLY__"

        # 4) Exit
        elif choice == "4":
            print(f"{BLUE}Exiting.{RESET}")
            sys.exit(0)

        else:
            print(f"{YELLOW}Invalid choice.{RESET}")


# ---------------------------------------------------------
# Pipeline Steps Selection
# ---------------------------------------------------------
def choose_steps(status: dict[str, str], no_ui: bool) -> list[int]:
    slug = status["slug"]
    profile = status["profile"]

    print()
    print(f"{BOLD}{CYAN}Pipeline status for slug={WHITE}{slug}{RESET}{CYAN}, profile={WHITE}{profile}{RESET}")
    print(f"{WHITE}[1]{RESET} txt+mp3           -> {status['1']}")
    print(f"{WHITE}[2]{RESET} stems/mix         -> {status['2']}")
    print(f"{WHITE}[3]{RESET} timings           -> {status['3']}")
    print(f"{WHITE}[4]{RESET} mp4               -> {status['4']}")
    print(f"{WHITE}[5]{RESET} upload            -> {status['5']}")
    print()

    if no_ui:
        # Default: run everything missing
        default = "".join([k for k, v in status.items() if k.isdigit() and v == "MISSING"])
        default = default or ""
        return [int(x) for x in default] if default else []

    # Suggest defaults:
    if status["1"] == "DONE" and status["2"] == "DONE" and status["3"] == "DONE":
        default = "45"
    elif status["1"] != "DONE":
        default = "1234"
    else:
        default = "234"

    raw = input(
        f"{WHITE}Steps to run (1=txt/mp3,2=stems,3=timing,4=mp4,5=upload,0=none){RESET} "
        f"{CYAN}[ENTER for {default}]{RESET}: "
    ).strip()

    if not raw:
        raw = default
    if raw == "0":
        return []

    out = []
    for ch in raw:
        if ch.isdigit():
            i = int(ch)
            if 1 <= i <= 5 and i not in out:
                out.append(i)
    return out


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    args = parse_args()

    no_ui = args.no_ui
    profile = args.profile
    query_from_cli = args.query
    slug_from_cli = args.slug

    # -----------------------------------------
    # Determine slug + query using menu logic
    # -----------------------------------------
    if slug_from_cli:
        slug = slugify(slug_from_cli)
        query = query_from_cli
        log("SLUG", f'Using slug="{WHITE}{slug}{RESET}"', CYAN)

    elif query_from_cli:
        slug = slugify(query_from_cli)
        query = query_from_cli
        log("SLUG", f'Using slug="{WHITE}{slug}{RESET}" (from query)', CYAN)

    else:
        # No slug + no query → show the menu
        slug, query = slug_selection_menu(args)

        if query == "__PIPELINE_ONLY__":
            # Only jump to pipeline menu; do NOT run Step1
            query = None

    # -----------------------------------------
    # OFFSET
    # -----------------------------------------
    if args.offset is not None:
        offset = args.offset
        write_offset(slug, offset)
        log("OFFSET", f"Using CLI offset={offset:+.3f}s", YELLOW)
    else:
        offset = read_offset(slug)
        log("OFFSET", f"Using stored offset={offset:+.3f}s", CYAN)

    # -----------------------------------------
    # Detect pipeline status
    # -----------------------------------------
    status = detect_step_status(slug, profile)

    # -----------------------------------------
    # Step choice (Pipeline menu)
    # -----------------------------------------
    if args.steps:
        steps = []
        for ch in args.steps:
            if ch.isdigit():
                i = int(ch)
                if 1 <= i <= 5 and i not in steps:
                    steps.append(i)
        log("MASTER", f"Running requested steps: {WHITE}{steps}{RESET}", BLUE)
    else:
        steps = choose_steps(status, no_ui)
        log("MASTER", f"Running steps: {WHITE}{steps}{RESET}", BLUE)

    # -----------------------------------------
    # Run steps
    # -----------------------------------------
    t1 = t2 = t3 = t4 = t5 = 0.0

    if 1 in steps and query is not None:
        t1 = run_step1(slug, query, no_ui)

    if 2 in steps:
        t2 = run_step2(slug, profile, args.model, interactive=not no_ui, no_ui=no_ui)

    if 3 in steps:
        t3 = run_step3(slug, no_ui)

    if 4 in steps:
        t4 = run_step4(slug, profile, offset, force=args.force_mp4, no_ui=no_ui)

    if 5 in steps and not args.no_upload:
        t5 = run_step5(slug, profile, offset, no_ui)
    elif 5 in steps and args.no_upload:
        log("STEP5", "Upload requested but --no-upload was set; skipping.", YELLOW)

    # -----------------------------------------
    # Summary
    # -----------------------------------------
    total = t1 + t2 + t3 + t4 + t5
    if total > 0:
        print()
        print(f"{BOLD}{BLUE}======== PIPELINE SUMMARY ========{RESET}")
        if t1:
            print(f"{WHITE}Step1 txt/mp3:{RESET}  {fmt_secs(t1)}")
        if t2:
            print(f"{WHITE}Step2 stems:{RESET}    {fmt_secs(t2)}")
        if t3:
            print(f"{WHITE}Step3 timing:{RESET}   {fmt_secs(t3)}")
        if t4:
            print(f"{WHITE}Step4 mp4:{RESET}      {fmt_secs(t4)}")
        if t5:
            print(f"{WHITE}Step5 upload:{RESET}   {fmt_secs(t5)}")
        print(f"{GREEN}Total time:     {fmt_secs(total)}{RESET}")
        print(f"{BOLD}{BLUE}===============================\n{RESET}")


if __name__ == "__main__":
    main()

# end of 0_master.py
