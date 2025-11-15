#!/usr/bin/env python3
# scripts/0_master.py
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
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
MIXES_DIR = BASE_DIR / "mixes"
META_DIR = BASE_DIR / "meta"
OUTPUT_DIR = BASE_DIR / "output"


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


def detect_latest_slug() -> str | None:
    if not META_DIR.exists():
        return None
    files = sorted(META_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


def detect_step_status(slug: str, profile: str) -> dict[str, str]:
    status = {
        "slug": slug,
        "profile": profile,
    }

    # Step 1: txt/mp3/meta
    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    meta = META_DIR / f"{slug}.json"
    if mp3.exists() and txt.exists() and meta.exists():
        status["1"] = "DONE"
    else:
        status["1"] = "MISSING"

    # Step 2: mix wav
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    if mix_wav.exists():
        status["2"] = "DONE"
    else:
        status["2"] = "MISSING"

    # Step 3: timings
    csv = TIMINGS_DIR / f"{slug}.csv"
    status["3"] = "DONE" if csv.exists() else "MISSING"

    # Step 4: mp4
    mp4s = list(OUTPUT_DIR.glob(f"{slug}_{profile}_offset_*.mp4"))
    status["4"] = "DONE" if mp4s else "MISSING"

    # Step 5: upload
    uploaded = META_DIR / f"{slug}_{profile}_uploaded.json"
    status["5"] = "DONE" if uploaded.exists() else "MISSING"

    return status


def prompt_yes_no(msg: str, default_yes=True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{msg} [{default}]: ").strip().lower()
        if not ans:
            return default_yes
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer y or n.")


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
def run_step1(slug: str, query: str | None) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    meta = META_DIR / f"{slug}.json"

    if mp3.exists() and txt.exists() and meta.exists():
        log("STEP1", "Already have txt/mp3/meta — skipping.", GREEN)
        return 0.0

    cmd = [sys.executable, str(SCRIPTS_DIR / "1_txt_mp3.py"), "--slug", slug]
    if query:
        cmd += ["--query", query]

    return run(cmd, "STEP1")


# ---------------------------------------------------------
# Step 2  (Demucs → 2_stems mix → render)
# ---------------------------------------------------------
def run_step2(slug: str, profile: str, model: str, interactive: bool) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"

    # Ask if user wants to bypass full separation.
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

    # Otherwise, full Demucs separation.
    if profile == "karaoke":
        effective_model = "htdemucs_6s"
        two_stems = False
    else:
        effective_model = model
        two_stems = True

    stems_root = BASE_DIR / "separated" / effective_model
    stems_dir = stems_root / slug

    stems_exist = stems_dir.exists() and any(stems_dir.glob("*.wav"))
    if stems_exist:
        reuse = prompt_yes_no("Stems exist. Reuse?", True)
        if not reuse:
            for p in stems_dir.glob("*.wav"):
                try:
                    p.unlink()
                except:
                    pass
            stems_exist = False

    if not stems_exist:
        # Run Demucs
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
        "--mp3",
        str(mp3),
        "--profile",
        profile,
        "--model",
        effective_model,
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
        "--mp3",
        str(mp3),
        "--profile",
        profile,
        "--model",
        effective_model,
        "--render-only",
        "--output",
        str(mix_wav),
    ]
    t_render = run(cmd, "STEP2-RENDER")

    return t_sep + t_ui + t_render


# ---------------------------------------------------------
# Step 3  (auto-timing or manual timing)
# ---------------------------------------------------------
def run_step3(slug: str) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    auto_script = SCRIPTS_DIR / "3_auto_timing.py"

    if auto_script.exists():
        cmd = [
            sys.executable,
            str(auto_script),
            "--slug",
            slug,
            "--mp3",
            str(mp3),
            "--txt",
            str(txt),
        ]
        return run(cmd, "STEP3-AUTO")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "3_timing.py"),
        "--txt",
        str(txt),
        "--audio",
        str(mp3),
        "--timings",
        str(TIMINGS_DIR / f"{slug}.csv"),
    ]
    return run(cmd, "STEP3")


# ---------------------------------------------------------
# Step 4  (mp4)
# ---------------------------------------------------------
def run_step4(slug: str, profile: str, offset: float, force: bool, called_from_master=True) -> float:
    """
    If called_from_master=True, pass --no-post-ui so 4_mp4 won't show the
    upload menu or file-open menu. Master will handle upload.
    """
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "4_mp4.py"),
        "--slug",
        slug,
        "--profile",
        profile,
        "--offset",
        str(offset),
    ]
    if force:
        cmd.append("--force")
    if called_from_master:
        cmd.append("--no-post-ui")

    return run(cmd, "STEP4")


# ---------------------------------------------------------
# Step 5  (upload)
# ---------------------------------------------------------
def run_step5(slug: str, profile: str, offset: float) -> float:
    # If upload metadata already exists, skip
    uploaded_json = META_DIR / f"{slug}_{profile}_uploaded.json"
    if uploaded_json.exists():
        log("STEP5", "Upload already done earlier — skipping.", GREEN)
        return 0.0

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "5_upload.py"),
        "--slug",
        slug,
        "--profile",
        profile,
        "--offset",
        str(offset),
        "--privacy",
        "private",
    ]
    return run(cmd, "STEP5")


# ---------------------------------------------------------
# Parse args
# ---------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Master orchestrator for Karaoke Time pipeline.")

    p.add_argument("--slug", type=str)
    p.add_argument("--query", type=str)
    p.add_argument("--profile", type=str, default="karaoke")
    p.add_argument("--model", type=str, default="htdemucs")
    p.add_argument("--offset", type=float, default=None)
    p.add_argument("--steps", type=str, help="E.g. 12345 or 45")
    p.add_argument("--skip-ui", action="store_true")
    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--force-mp4", action="store_true")

    return p.parse_args()


# ---------------------------------------------------------
# Choose steps
# ---------------------------------------------------------
def choose_steps(status: dict[str, str]) -> list[int]:
    slug = status["slug"]
    profile = status["profile"]

    print()
    print(f"{BOLD}Pipeline status for slug={slug}, profile={profile}{RESET}")
    print(f"[1] txt+mp3           -> {status['1']}")
    print(f"[2] stems/mix         -> {status['2']}")
    print(f"[3] timings           -> {status['3']}")
    print(f"[4] mp4               -> {status['4']}")
    print(f"[5] upload            -> {status['5']}")
    print()

    # Suggest defaults:
    if status["1"] == "DONE" and status["2"] == "DONE" and status["3"] == "DONE":
        default = "45"
    elif status["1"] != "DONE":
        default = "1234"
    else:
        default = "234"

    s = input(
        "Steps to run (1=txt/mp3,2=stems,3=timing,4=mp4,5=upload,0=none, "
        f"ENTER for suggested={default}): "
    ).strip()

    if not s:
        s = default

    if s == "0":
        return []

    out = []
    for ch in s:
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

    # SLUG SELECTION
    if args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using slug="{slug}"', CYAN)
    elif args.query:
        new_slug = slugify(args.query)
        old_slug = detect_latest_slug()
        if old_slug and old_slug != new_slug:
            ans = input(
                f'Previous slug "{old_slug}" found. Use that instead of new "{new_slug}"? [y/N]: '
            ).strip().lower()
            slug = old_slug if ans == "y" else new_slug
        else:
            slug = new_slug
        log("SLUG", f'Using slug="{slug}"', CYAN)
    else:
        slug = detect_latest_slug()
        if not slug:
            print(f"{RED}No slug provided, no query, no prior metadata. Exiting.{RESET}")
            sys.exit(1)
        log("SLUG", f'Inferred slug="{slug}"', CYAN)

    profile = args.profile
    query = args.query

    # OFFSET
    if args.offset is not None:
        offset = args.offset
        write_offset(slug, offset)
        log("OFFSET", f"Using CLI offset={offset:+.3f}s", YELLOW)
    else:
        offset = read_offset(slug)
        log("OFFSET", f"Using stored offset={offset:+.3f}s", CYAN)

    # STATUS + STEP SELECTION
    status = detect_step_status(slug, profile)

    if args.steps:
        steps = []
        for ch in args.steps:
            if ch.isdigit():
                i = int(ch)
                if 1 <= i <= 5 and i not in steps:
                    steps.append(i)
        log("MASTER", f"Running requested steps: {steps}")
    else:
        steps = choose_steps(status)
        log("MASTER", f"Running steps: {steps}")

    # RUN
    t1 = t2 = t3 = t4 = t5 = 0.0

    if 1 in steps:
        t1 = run_step1(slug, query)

    if 2 in steps:
        t2 = run_step2(slug, profile, args.model, interactive=not args.skip_ui)

    if 3 in steps:
        t3 = run_step3(slug)

    if 4 in steps:
        t4 = run_step4(slug, profile, offset, force=args.force_mp4, called_from_master=True)

    if 5 in steps and not args.no_upload:
        t5 = run_step5(slug, profile, offset)
    elif 5 in steps and args.no_upload:
        log("STEP5", "Upload requested but --no-upload was set; skipping.", YELLOW)

    # SUMMARY
    total = t1 + t2 + t3 + t4 + t5
    if total > 0:
        print()
        print(f"{BOLD}{BLUE}======== PIPELINE SUMMARY ========{RESET}")
        if t1:
            print(f"Step1 txt/mp3:  {fmt_secs(t1)}")
        if t2:
            print(f"Step2 stems:    {fmt_secs(t2)}")
        if t3:
            print(f"Step3 timing:   {fmt_secs(t3)}")
        if t4:
            print(f"Step4 mp4:      {fmt_secs(t4)}")
        if t5:
            print(f"Step5 upload:   {fmt_secs(t5)}")
        print(f"{GREEN}Total time:     {fmt_secs(total)}{RESET}")
        print(f"{BOLD}{BLUE}===============================\n{RESET}")


if __name__ == "__main__":
    main()

# end of 0_master.py
