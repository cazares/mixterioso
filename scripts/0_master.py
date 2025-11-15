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
MP3_DIR = BASE_DIR / "mp3s"
TXT_DIR = BASE_DIR / "txts"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
MIXES_DIR = BASE_DIR / "mixes"
META_DIR = BASE_DIR / "meta"
OUTPUT_DIR = BASE_DIR / "output"


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def fmt_secs_mmss(sec: float) -> str:
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    return f"{m:02d}:{s:02d}"


def detect_latest_slug() -> str | None:
    """
    Try to infer latest slug by looking at meta/*.json files.
    """
    if not META_DIR.exists():
        return None
    json_files = sorted(META_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not json_files:
        return None
    newest = json_files[0]
    return newest.stem


def detect_step_status(slug: str, profile: str) -> dict[str, str]:
    """
    Look at the filesystem and infer which steps are done/missing.
    """
    status: dict[str, str] = {}

    # Step 1: txt+mp3
    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    if mp3_path.exists() and txt_path.exists() and meta_path.exists():
        status["1"] = "DONE"
    else:
        status["1"] = "MISSING"

    # Step 2: stems/mix
    mix_config = MIXES_DIR / f"{slug}_{profile}.json"
    mix_audio = MIXES_DIR / f"{slug}_{profile}.wav"
    if mix_audio.exists():
        status["2"] = "DONE"
    elif mix_config.exists():
        status["2"] = "CONFIG_ONLY"
    else:
        status["2"] = "MISSING"

    # Step 3: timings
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    if timing_path.exists():
        status["3"] = "DONE"
    else:
        status["3"] = "MISSING"

    # Step 4: mp4
    mp4_candidates = list(OUTPUT_DIR.glob(f"{slug}_{profile}_offset_*.mp4"))
    if mp4_candidates:
        status["4"] = "DONE"
    else:
        status["4"] = "MISSING"

    # Step 5: upload
    upload_meta = META_DIR / f"{slug}_{profile}_upload.json"
    if upload_meta.exists():
        status["5"] = "DONE"
    else:
        status["5"] = "MISSING"

    return status


def prompt_yes_no(prompt: str, default_yes: bool = True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{prompt} [{default}]: ").strip().lower()
        if not ans:
            return default_yes
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no"}:
            return False
        print("Please answer y or n.")


def run(cmd: list[str], section: str) -> float:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    return t1 - t0


def run_capture(cmd: list[str], section: str) -> tuple[float, str]:
    """
    Like run(), but captures stdout (for JSON from 5_upload.py).
    """
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
    t1 = time.perf_counter()
    return t1 - t0, cp.stdout


def read_offset(slug: str) -> float:
    """
    Read offset seconds from offsets/<slug>.txt if present, else 0.0
    """
    offset_file = OFFSETS_DIR / f"{slug}.txt"
    if not offset_file.exists():
        return 0.0
    try:
        value = float(offset_file.read_text().strip())
        return value
    except Exception:
        return 0.0


def write_offset(slug: str, offset: float) -> None:
    OFFSETS_DIR.mkdir(parents=True, exist_ok=True)
    (OFFSETS_DIR / f"{slug}.txt").write_text(f"{offset:.3f}\n")


def run_step1_txt_mp3(slug: str, query: str | None) -> float:
    """
    Step 1: ensure mp3 + txt + meta exist.
    """
    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"

    have_all = mp3_path.exists() and txt_path.exists() and meta_path.exists()
    if have_all:
        log("STEP1", f"txt/mp3/meta already exist for slug={slug}, skipping.", GREEN)
        return 0.0

    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "1_txt_mp3.py"),
    ]
    if query:
        cmd += ["--query", query]
    cmd += ["--slug", slug]

    t = run(cmd, "STEP1")
    return t


def run_step2_stems(slug: str, profile: str, model: str, interactive: bool = True) -> float:
    """
    Step 2: Run Demucs + mix UI + render mix.

    IMPORTANT:
    - Karaoke needs 6-stem Demucs (bass/drums/guitar/piano/other/vocals), so we
      force model=htdemucs_6s for that profile to match 2_stems mix configs.
    - Simple profiles can still use 2-stem for speed.
    """
    mp3_path = MP3_DIR / f"{slug}.mp3"
    mix_config = MIXES_DIR / f"{slug}_{profile}.json"
    mix_audio = MIXES_DIR / f"{slug}_{profile}.wav"

    # ---- NEW: quick bypass if user wants 100% original full mix ----
    # If they say yes, we just convert mp3 -> wav and skip Demucs + 2_stems.
    if interactive:
        use_bypass = prompt_yes_no(
            "Use original full mix (100% all tracks, skip Demucs stem separation)?",
            False,
        )
    else:
        use_bypass = False

    if use_bypass:
        MIXES_DIR.mkdir(parents=True, exist_ok=True)
        if mix_audio.exists():
            log("STEP2", f"Bypass mix already exists at {mix_audio}", GREEN)
            return 0.0
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(mp3_path),
            str(mix_audio),
        ]
        t_bypass = run(ffmpeg_cmd, "STEP2-BYPASS")
        return t_bypass
    # ---- END NEW BYPASS BLOCK ----

    # Decide effective Demucs model + whether to use two-stems
    effective_model = model
    if profile == "karaoke":
        if model != "htdemucs_6s":
            log(
                "STEP2",
                f"Profile 'karaoke' detected — overriding Demucs model "
                f"from '{model}' to 'htdemucs_6s' for 6-stem output.",
                YELLOW,
            )
        effective_model = "htdemucs_6s"
        demucs_two_stems = False
    else:
        demucs_two_stems = True

    stems_root = BASE_DIR / "separated" / effective_model
    stems_dir = stems_root / slug
    stems_exist = stems_dir.exists() and any(stems_dir.glob("*.wav"))

    if stems_exist:
        reuse = prompt_yes_no("Stems exist for this slug. Reuse and skip separation?", True)
        if not reuse:
            log("STEP2", "Will regenerate stems.", YELLOW)
            for p in stems_dir.glob("*.wav"):
                try:
                    p.unlink()
                except OSError:
                    pass
            stems_exist = False

    if not stems_exist:
        # Run Demucs
        demucs_cmd = [
            sys.executable,
            "-m",
            "demucs",
            "-n",
            effective_model,
        ]
        if demucs_two_stems:
            demucs_cmd += ["--two-stems", "vocals"]
            section = "STEP2-2STEM"
        else:
            section = "STEP2-6STEM"

        demucs_cmd.append(str(mp3_path))
        t_sep = run(demucs_cmd, section)
    else:
        t_sep = 0.0
        log("STEP2", "Reusing existing stems.", GREEN)

    # Mix UI
    mix_ui_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3",
        str(mp3_path),
        "--profile",
        profile,
        "--model",
        effective_model,
        "--mix-ui-only",
    ]

    if not interactive:
        mix_ui_cmd.append("--non-interactive")

    t_ui = run(mix_ui_cmd, "STEP2-MIXUI")

    # Render mix
    MIXES_DIR.mkdir(parents=True, exist_ok=True)
    output_mix = MIXES_DIR / f"{slug}_{profile}.wav"
    mix_render_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3",
        str(mp3_path),
        "--profile",
        profile,
        "--model",
        effective_model,
        "--render-only",
        "--output",
        str(output_mix),
    ]
    t_render = run(mix_render_cmd, "STEP2-RENDER")

    return t_sep + t_ui + t_render


def run_step3_timing(slug: str) -> float:
    """
    Step 3: timings.
    - Prefer 3_auto_timing.py if present.
    - Otherwise fall back to 3_timing.py.
    """
    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    timing_path.parent.mkdir(parents=True, exist_ok=True)

    auto_script = SCRIPTS_DIR / "3_auto_timing.py"
    if auto_script.exists():
        cmd = [
            sys.executable,
            str(auto_script),
            "--slug",
            slug,
            "--mp3",
            str(mp3_path),
            "--txt",
            str(txt_path),
        ]
        # 3_auto_timing writes timings/<slug>.csv itself with canonical header.
        section = "STEP3-AUTO"
    else:
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "3_timing.py"),
            "--txt",
            str(txt_path),
            "--audio",
            str(mp3_path),
            "--timings",
            str(timing_path),
        ]
        section = "STEP3"

    t = run(cmd, section)
    return t


def run_step4_mp4(slug: str, profile: str, offset: float, force_mp4: bool) -> float:
    """
    Step 4: generate mp4 via 4_mp4.py.

    If force_mp4 is True, passes --force through so 4_mp4.py regenerates
    even when an output mp4 already exists.
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
    if force_mp4:
        cmd.append("--force")

    try:
        t = run(cmd, "STEP4")
        return t
    except subprocess.CalledProcessError as e:
        log(
            "STEP4",
            f"mp4 generation failed for slug={slug}, profile={profile} "
            f"with return code {e.returncode}",
            RED,
        )
        raise


def run_step5_upload(slug: str, profile: str, offset: float) -> float:
    """
    Step 5: Upload to YouTube using scripts/5_upload.py.

    This variant assumes 5_upload.py takes slug/profile/offset and
    emits JSON on stdout with e.g. {"video_url": "..."}.
    """
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "5_upload.py"),
        "--slug",
        slug,
        "--profile",
        profile,
        "--offset",
        str(offset),
    ]
    t, out = run_capture(cmd, "STEP5")
    out = out.strip()
    if out:
        try:
            data = json.loads(out)
            video_url = data.get("video_url") or data.get("url")
            if video_url:
                log("STEP5", f"Uploaded to YouTube: {video_url}", GREEN)
            else:
                log("STEP5", f"Upload result: {data}", GREEN)
        except json.JSONDecodeError:
            log("STEP5", f"Non-JSON output:\n{out}", YELLOW)
    return t


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Master orchestrator for Karaoke Time pipeline.")

    parser.add_argument(
        "--slug",
        type=str,
        help="Song slug (default: inferred or derived from query).",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Search query for YouTube (used only if step 1 runs, and for slug suggestion).",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        help="Mix profile (e.g., karaoke, vocals_only, no_vocals, ...).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="htdemucs",
        help="Demucs model name (e.g., htdemucs, htdemucs_ft, htdemucs_6s).",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=None,
        help="Override audio/video offset in seconds (default: use offsets/<slug>.txt or 0).",
    )
    parser.add_argument(
        "--steps",
        type=str,
        default=None,
        help="Steps to run (e.g., 1,2,3,4,5 or 45). If omitted, you will be prompted.",
    )
    parser.add_argument(
        "--skip-ui",
        action="store_true",
        help="Skip interactive mix UI for stems (step 2).",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip upload step 5 even if selected.",
    )
    parser.add_argument(
        "--force-mp4",
        action="store_true",
        help="Force mp4 regeneration even if output already exists (passes --force to 4_mp4.py).",
    )
    return parser.parse_args()


def choose_steps(status: dict[str, str]) -> list[int]:
    """
    Show current status and prompt user for steps to run.
    """
    print()
    print(f"{BOLD}Pipeline status for slug={status.get('slug')}, profile={status.get('profile')}{RESET}")

    print(f"[1] txt+mp3 generation (1_txt_mp3)           -> {status.get('1', 'UNKNOWN')}")
    print(f"[2] stems/mix (Demucs + mix UI)             -> {status.get('2', 'UNKNOWN')}")
    print(f"[3] timings CSV (3_timing/3_auto_timing)    -> {status.get('3', 'UNKNOWN')}")
    print(f"[4] mp4 generation (4_mp4)                  -> {status.get('4', 'UNKNOWN')}")
    print(f"[5] YouTube upload (5_upload)               -> {status.get('5', 'UNKNOWN')}")
    print()

    # Suggested default: if txt/mp3+stems+timings exist, suggest 45 (mp4 + upload)
    if status.get("1") == "DONE" and status.get("2") == "DONE" and status.get("3") == "DONE":
        default = "45"
    elif status.get("1") != "DONE":
        default = "1234"
    else:
        default = "234"

    steps_str = input(
        "Steps to run (1=txt/mp3,2=stems,3=timing,4=mp4,5=upload, 0=none, "
        f"ENTER for suggested={default}): "
    ).strip()

    if not steps_str:
        steps_str = default

    if steps_str == "0":
        return []

    steps: list[int] = []
    for ch in steps_str:
        if ch.isdigit():
            step = int(ch)
            if 1 <= step <= 5 and step not in steps:
                steps.append(step)
    return steps


def main() -> None:
    args = parse_args()

    # ----- Slug selection (with "reuse previous slug?" behavior) -----
    if args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using explicit slug: "{slug}"', CYAN)
    elif args.query:
        # New slug suggestion from query
        suggested_slug = slugify(args.query)
        latest_slug = detect_latest_slug()

        if latest_slug and latest_slug != suggested_slug:
            ans = input(
                f'Previous slug "{latest_slug}" found. '
                f'Use that instead of new slug "{suggested_slug}" from query? [y/N]: '
            ).strip().lower()
            if ans in {"y", "yes"}:
                slug = latest_slug
                log("SLUG", f'Using previous slug: "{slug}"', CYAN)
            else:
                slug = suggested_slug
                log("SLUG", f'Using new slug from query: "{slug}"', CYAN)
        else:
            slug = suggested_slug
            log("SLUG", f'Using slug from query: "{slug}"', CYAN)
    else:
        slug = detect_latest_slug()
        if not slug:
            print(f"{RED}No slug provided, no query, and could not infer latest from meta/.{RESET}")
            sys.exit(1)
        log("SLUG", f'Inferred latest slug: "{slug}"', CYAN)

    profile = args.profile
    model = args.model

    # Step 1 may use a YouTube search query to fetch txt/mp3/meta
    query = args.query

    # Offset: CLI overrides stored offset
    if args.offset is not None:
        offset = args.offset
        log("OFFSET", f"Using CLI override offset={offset:+.3f}s", YELLOW)
        write_offset(slug, offset)
    else:
        offset = read_offset(slug)
        log("OFFSET", f"Using stored offset={offset:+.3f}s", CYAN)

    # Build status and choose steps
    status = detect_step_status(slug, profile)
    status["slug"] = slug
    status["profile"] = profile

    if args.steps:
        steps = []
        for ch in args.steps:
            if ch.isdigit():
                step = int(ch)
                if 1 <= step <= 5 and step not in steps:
                    steps.append(step)
        log("MASTER", f"Running requested steps: {steps}", CYAN)
    else:
        steps = choose_steps(status)
        log("MASTER", f"Running steps: {steps}", CYAN)

    total_t = 0.0
    t1 = t2 = t3 = t4 = t5 = 0.0

    if 1 in steps:
        t1 = run_step1_txt_mp3(slug, query)
        total_t += t1

    if 2 in steps:
        t2 = run_step2_stems(slug, profile, model, interactive=not args.skip_ui)
        total_t += t2

    if 3 in steps:
        t3 = run_step3_timing(slug)
        total_t += t3

    if 4 in steps:
        t4 = run_step4_mp4(slug, profile, offset, force_mp4=args.force_mp4)
        total_t += t4

    if 5 in steps and not args.no_upload:
        t5 = run_step5_upload(slug, profile, offset)
        total_t += t5
    elif 5 in steps and args.no_upload:
        log("STEP5", "Upload step requested but --no-upload is set; skipping.", YELLOW)

    if total_t > 0:
        print()
        print(f"{BOLD}{BLUE}================= PIPELINE TIMINGS ================={RESET}")
        if t1:
            print(f"{CYAN}Step 1 txt/mp3:  {fmt_secs_mmss(t1)}{RESET}")
        if t2:
            print(f"{CYAN}Step 2 stems:    {fmt_secs_mmss(t2)}{RESET}")
        if t3:
            print(f"{CYAN}Step 3 timing:   {fmt_secs_mmss(t3)}{RESET}")
        if t4:
            print(f"{CYAN}Step 4 mp4:      {fmt_secs_mmss(t4)}{RESET}")
        if t5:
            print(f"{CYAN}Step 5 upload:   {fmt_secs_mmss(t5)}{RESET}")
        print(f"{BOLD}{GREEN}Total pipeline: {fmt_secs_mmss(total_t)}{RESET}")
        print(f"{BOLD}{BLUE}====================================================={RESET}")


if __name__ == "__main__":
    main()
# end of 0_master.py
