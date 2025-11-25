#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import sys, os
PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT)

from scripts.mix_utils import load_existing_config

# ==========================================================
# COLORS
# ==========================================================
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
WHITE  = "\033[97m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"
MAG    = "\033[35m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ==========================================================
# PATHS
# ==========================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR     = BASE_DIR / "txts"
MP3_DIR     = BASE_DIR / "mp3s"
MIXES_DIR   = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR  = BASE_DIR / "output"
META_DIR    = BASE_DIR / "meta"
UPLOAD_DIR  = BASE_DIR / "uploaded"   # upload receipts

UPLOAD_DIR.mkdir(exist_ok=True)


# ==========================================================
# HELPERS
# ==========================================================
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def fmt_secs_mmss(sec: float) -> str:
    if sec <= 0:
        return "   0.00 s  (00:00)"
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{sec:7.2f} s  ({m:02d}:{s:02d})"


def run(cmd: list[str], section: str) -> float:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0


# ==========================================================
# SLUG DETECTION
# ==========================================================
def detect_slug_from_latest_mp3() -> str:
    metas = sorted(META_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if metas:
        slug = slugify(metas[-1].stem)
        log("MASTER", f"Latest slug from meta/: {slug}", CYAN)
        return slug

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found in mp3s/")
    slug = slugify(mp3s[-1].stem)
    log("MASTER", f"Latest slug from mp3s/: {slug}", CYAN)
    return slug


# ==========================================================
# META LOADING
# ==========================================================
def load_meta(slug: str):
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return None, None
    try:
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        return m.get("artist"), m.get("title")
    except Exception:
        return None, None


# ==========================================================
# ASSET DETECTION
# ==========================================================
def detect_assets(slug: str, profile: str) -> dict:
    txt         = TXT_DIR / f"{slug}.txt"
    mp3         = MP3_DIR / f"{slug}.mp3"
    meta        = META_DIR / f"{slug}.json"
    mix_wav     = MIXES_DIR / f"{slug}_{profile}.wav"
    timing_csv  = TIMINGS_DIR / f"{slug}.csv"
    mp4         = OUTPUT_DIR / f"{slug}_{profile}.mp4"
    receipt     = UPLOAD_DIR / f"{slug}_{profile}.uploaded"

    step1 = txt.exists() and mp3.exists() and meta.exists()
    step2 = True if profile == "lyrics" else mix_wav.exists()
    step3 = timing_csv.exists()
    step4 = mp4.exists()
    step5 = receipt.exists()

    return {1: step1, 2: step2, 3: step3, 4: step4, 5: step5}


# ==========================================================
# STATUS PRINTING
# ==========================================================
def print_asset_status(slug: str, profile: str, status: dict) -> None:
    print()
    print(f"{BOLD}{WHITE}Pipeline status for slug={slug}, profile={profile}{RESET}")
    labels = {
        1: "txt+mp3 generation (1_txt_mp3)",
        2: "stems/mix (Demucs + mix UI)",
        3: "timings CSV (3_timing)",
        4: "mp4 generation (4_mp4)",
        5: "upload to YouTube (5_upload)",
    }
    for step in range(1, 6):
        done  = status.get(step, False)
        color = GREEN if done else YELLOW
        mark  = "DONE" if done else "MISSING"
        print(f"{color}[{step}] {labels[step]}  -> {mark}{RESET}")
    print()


def suggest_steps(status: dict) -> str:
    missing = "".join(str(k) for k in range(1, 6) if not status.get(k))
    return missing or "0"


def parse_steps_string(s: str) -> list[int]:
    steps: list[int] = []
    for ch in s:
        if ch in "012345":
            val = int(ch)
            if val not in steps:
                steps.append(val)
    steps.sort()
    return steps


# ==========================================================
# STEP 1
# ==========================================================
def run_step1_txt_mp3(query: str) -> tuple[str, float]:
    t = run([sys.executable, str(SCRIPTS_DIR / "1_txt_mp3.py"), query], "STEP1")
    slug = detect_slug_from_latest_mp3()
    log("STEP1", f"Slug detected: {slug}", GREEN)
    return slug, t


# ==========================================================
# STEP 2
# ==========================================================
def run_step2_stems(slug: str, profile: str, model: str, interactive: bool) -> float:
    """
    Step 2 now behaves in the correct order:
      1. Detect existing stems
      2. Ask user whether to reuse them BEFORE UI
      3. Run mix UI
      4. Render mix
      5. Only re-run Demucs if user declines reuse
    """

    if profile == "lyrics":
        log("STEP2", "Profile 'lyrics' skips stems/mix.", YELLOW)
        return 0.0

    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    if not mp3_path.exists() or not txt_path.exists():
        raise SystemExit(f"Missing assets for step 2: {mp3_path}, {txt_path}")

    separated_root = BASE_DIR / "separated"

    # Allowed models (strict 4-stem)
    preferred: list[str] = []
    if model:
        preferred.append(model)
    if "htdemucs" not in preferred:
        preferred.insert(0, "htdemucs")

    # Check for existing stems
    existing_model = None
    for m in preferred:
        d = separated_root / m / slug
        if d.exists():
            existing_model = m
            break

    reuse = False
    actual = existing_model

    # Ask BEFORE mix UI
    if existing_model and interactive:
        ans = input(
            f"Stems found for model '{existing_model}'. Reuse existing stems? [Y/n]: "
        ).strip().lower()
        reuse = ans in ("", "y", "yes")
    elif existing_model:
        reuse = True

    t_demucs = 0.0

    # Run Demucs only if needed
    if not reuse:
        import subprocess as sp
        log("STEP2", f"Running Demucs (4-stem) with models: {preferred}", CYAN)
        actual = None
        for m in preferred:
            try:
                t_demucs += run(["demucs", "-n", m, str(mp3_path)], "STEP2-DEMUX")
                actual = m
                break
            except sp.CalledProcessError:
                log("STEP2", f"Model '{m}' failed. Trying next...", YELLOW)
        if actual is None:
            raise SystemExit("Demucs failed for all allowed models.")
    elif actual is None:
        raise SystemExit("Reuse requested but no stems actually exist.")

    log("STEP2", f"Using stems from model '{actual}'", GREEN)

    # --- RUN MIX UI (now after stems decision) ---
    t_ui = run(
        [
            sys.executable, str(SCRIPTS_DIR / "2_stems.py"),
            "--mp3", str(mp3_path),
            "--profile", profile,
            "--model", actual,
            "--mix-ui-only",
        ],
        "STEP2-MIXUI",
    )

    out_wav = MIXES_DIR / f"{slug}_{profile}.wav"

    # --- RENDER MIX ---
    t_render = run(
        [
            sys.executable, str(SCRIPTS_DIR / "2_stems.py"),
            "--mp3", str(mp3_path),
            "--profile", profile,
            "--model", actual,
            "--render-only",
            "--output", str(out_wav),
        ],
        "STEP2-RENDER",
    )

    total = t_demucs + t_ui + t_render
    log("STEP2", f"Completed in {fmt_secs_mmss(total)}", GREEN)
    return total

# ==========================================================
# STEP 3
# ==========================================================
def run_step3_timing(slug: str) -> float:
    mp3_path    = MP3_DIR / f"{slug}.mp3"
    txt_path    = TXT_DIR / f"{slug}.txt"
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    timing_path.parent.mkdir(exist_ok=True)

    t = run(
        [
            sys.executable, str(SCRIPTS_DIR / "3_timing.py"),
            "--txt", str(txt_path),
            "--audio", str(mp3_path),
            "--timings", str(timing_path),
        ],
        "STEP3",
    )
    return t


# ==========================================================
# STEP 4
# ==========================================================
def run_step4_mp4(slug: str, profile: str) -> float:
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "4_mp4.py"),
        "--slug", slug,
        "--profile", profile,
    ]
    try:
        return run(cmd, "STEP4")
    except subprocess.CalledProcessError:
        log("STEP4", "Step 4 failed. Most likely mixed WAV missing. Run step 2 first.", RED)
        return 0.0


# ==========================================================
# STEP 5 (UPLOAD)
# ==========================================================
def run_step5_upload(slug: str, profile: str) -> float:
    """
    Upload to YouTube using 5_upload.py.

    Includes:
      - Colorized upload summary
      - Relative paths
      - MM:SS durations
      - Previous-upload detection with count
      - Yes/no confirmation
      - Pretty descriptor prompt with autosuggestions
    """
    # -----------------------------------------
    # Load metadata
    # -----------------------------------------
    artist, title = load_meta(slug)
    artist = artist or "Unknown Artist"
    title  = title or slug.replace("_", " ").title()

    # -----------------------------------------
    # Load mix config (volumes + model)
    # -----------------------------------------
    cfg_path = MIXES_DIR / f"{slug}_{profile}.json"
    model   = "unknown"
    volumes = {}
    if cfg_path.exists():
        try:
            cfg    = json.loads(cfg_path.read_text(encoding="utf-8"))
            model  = cfg.get("model", "unknown")
            volumes = cfg.get("volumes", {})
        except Exception:
            pass

    # -----------------------------------------
    # Paths (relative versions)
    # -----------------------------------------
    mp4_path = OUTPUT_DIR / f"{slug}_{profile}.mp4"
    wav_path = MIXES_DIR / f"{slug}_{profile}.wav"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    def rel(p: Path) -> str:
        try:
            return f"./{p.relative_to(BASE_DIR)}"
        except ValueError:
            return str(p)

    # -----------------------------------------
    # Compute durations
    # -----------------------------------------
    def compute_len(p: Path) -> float:
        if not p.exists():
            return 0.0
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(p),
                ],
                text=True,
            ).strip()
            return float(out)
        except Exception:
            return 0.0

    mp3_len = compute_len(mp3_path)
    mp4_len = compute_len(mp4_path)

    # -----------------------------------------
    # Previous upload detection
    # -----------------------------------------
    receipt_path = UPLOAD_DIR / f"{slug}_{profile}.uploaded"
    upload_count = 0
    if receipt_path.exists():
        try:
            prev = json.loads(receipt_path.read_text(encoding="utf-8"))
            upload_count = int(prev.get("count", 1))
        except Exception:
            upload_count = 1

    uploaded_before = upload_count > 0 and receipt_path.exists()

    if uploaded_before:
        log(
            "STEP5",
            f"Previously uploaded {upload_count} time(s); receipt: {receipt_path}",
            YELLOW,
        )
        try:
            ans = input("Upload again? [Y/n]: ").strip().lower()
        except EOFError:
            ans = "y"
        if ans not in ("", "y", "yes"):
            log("STEP5", "Upload skipped by user.", YELLOW)
            return 0.0

    # -----------------------------------------
    # Compute descriptor suggestions
    # -----------------------------------------
    def safe_vol(track: str, default: float = 1.0) -> float:
        try:
            return float(volumes.get(track, default))
        except Exception:
            return default

    v_vocals = safe_vol("vocals", 1.0)
    v_bass   = safe_vol("bass",   1.0)
    v_gtr    = safe_vol("guitar", 1.0)
    v_piano  = safe_vol("piano",  1.0)
    v_other  = safe_vol("other",  1.0)

    all_instr_100 = (
        abs(v_bass  - 1.0) < 1e-3 and
        abs(v_gtr   - 1.0) < 1e-3 and
        abs(v_piano - 1.0) < 1e-3 and
        abs(v_other - 1.0) < 1e-3
    )

    suggestions: list[str] = []

    # 0% vocals: classic Karaoke
    if abs(v_vocals) < 1e-3:
        suggestions.append("Karaoke")

    # Instruments 100%, some vocals → Car Karaoke / Karaoke
    if all_instr_100 and 0.0 < v_vocals < 1.0:
        if "car" in profile:
            suggestions.append(f"Car Karaoke, {int(round(v_vocals * 100))}% Vocals")
        else:
            suggestions.append(f"Karaoke, {int(round(v_vocals * 100))}% Vocals")

    # Non-100% instrument focus
    diff_instr = {
        "bass":   v_bass,
        "guitar": v_gtr,
        "piano":  v_piano,
        "other":  v_other,
    }
    varied = [k for k, v in diff_instr.items() if abs(v - 1.0) > 1e-3]
    if varied:
        primary = varied[0]
        label_map = {
            "bass":   "Bass",
            "guitar": "Guitar",
            "piano":  "Piano",
            "other":  "Band",
        }
        base_label = label_map.get(primary, primary.title())
        suggestions.append(f"{base_label} Karaoke, {int(round(v_vocals * 100))}% Vocals")

    # Lyrics / Letra style when everything is 100%
    if all_instr_100 and abs(v_vocals - 1.0) < 1e-3:
        suggestions.append("Lyrics")
        suggestions.append("Letra")

    # Fallback: profile-based
    if not suggestions:
        suggestions.append(profile.replace("-", " ").title())

    # Deduplicate, keep order
    dedup: list[str] = []
    for s in suggestions:
        if s not in dedup:
            dedup.append(s)
    suggestions = dedup

    default_desc = suggestions[0] if suggestions else profile.replace("-", " ").title()

    # -----------------------------------------
    # Pretty colorized upload summary
    # -----------------------------------------
    print()
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}UPLOAD SUMMARY for slug='{slug}'  (profile={profile}){RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")

    print(f"{CYAN}Artist:{RESET}       {WHITE}{artist}{RESET}")
    print(f"{CYAN}Title:{RESET}        {WHITE}{title}{RESET}")
    print(f"{CYAN}Slug:{RESET}         {WHITE}{slug}{RESET}")
    print(f"{CYAN}Profile:{RESET}      {WHITE}{profile}{RESET}")
    print(f"{CYAN}Model:{RESET}        {WHITE}{model}{RESET}")
    print(f"{CYAN}Stems:{RESET}        {WHITE}vocals, bass, guitar, piano, other{RESET}")

    print(f"{CYAN}Volumes:{RESET}")
    for stem, vol in volumes.items():
        try:
            pct = int(round(float(vol) * 100))
        except Exception:
            pct = 0
        print(f"  {WHITE}{stem:7s}{RESET}: {CYAN}{pct:3d}%{RESET}")

    print()
    print(f"{CYAN}MP3:{RESET}          {WHITE}{rel(mp3_path)}{RESET}")
    print(f"{CYAN}MP3 length:{RESET}   {WHITE}{fmt_secs_mmss(mp3_len)}{RESET}")
    print(f"{CYAN}WAV mix:{RESET}      {WHITE}{rel(wav_path)}{RESET}")
    print(f"{CYAN}MP4 output:{RESET}   {WHITE}{rel(mp4_path)}{RESET}")
    print(f"{CYAN}MP4 length:{RESET}   {WHITE}{fmt_secs_mmss(mp4_len)}{RESET}")

    if uploaded_before:
        print()
        print(f"{YELLOW}Previously uploaded {upload_count} time(s).{RESET}")

    print()
    print(f"{CYAN}Descriptor suggestions:{RESET}")
    for idx, s in enumerate(suggestions, start=1):
        print(f"  {WHITE}[{idx}] {s}{RESET}")

    # -----------------------------------------
    # Ask user for descriptor (with default)
    # -----------------------------------------
    print()
    try:
        raw = input(
            f"Enter upload descriptor "
            f"(or choose 1-{len(suggestions)}) "
            f"[default={default_desc}]: "
        ).strip()
    except EOFError:
        raw = ""

    desc: str
    if not raw:
        desc = default_desc
    else:
        # Support picking by number
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(suggestions):
                desc = suggestions[idx - 1]
            else:
                desc = raw
        else:
            desc = raw

    upload_title = f"{artist} – {title} ({desc})"

    print()
    print(f"{CYAN}Final YouTube TITLE will be:{RESET}")
    print(f"  {WHITE}{upload_title}{RESET}")

    try:
        ok = input("Continue with upload? [Y/n]: ").strip().lower()
    except EOFError:
        ok = "y"

    if ok not in ("", "y", "yes"):
        log("STEP5", "Upload cancelled by user.", YELLOW)
        return 0.0

    # -----------------------------------------
    # Run uploader script
    # -----------------------------------------
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "5_upload.py"),
        "--file", str(mp4_path),
        "--title", upload_title,
        "--description", "",
        "--privacy", "unlisted",
        "--thumb-from-sec", "0.5",
    ]

    t_upload = run(cmd, "STEP5")

    # -----------------------------------------
    # Write / update receipt
    # -----------------------------------------
    new_count = upload_count + 1
    receipt_data = {
        "slug": slug,
        "profile": profile,
        "count": new_count,
        "last_title": upload_title,
        "last_uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        log("STEP5", f"Upload receipt written: {receipt_path} (count={new_count})", GREEN)
    except Exception as e:
        log("STEP5", f"Failed to write upload receipt: {e}", YELLOW)

    return t_upload


# ==========================================================
# Argument Parsing
# ==========================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=(
            "Karaoke pipeline master "
            "(1=txt/mp3, 2=stems, 3=timing, 4=mp4, 5=upload)."
        )
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--query", type=str, help="Search query for step 1 (1_txt_mp3)")
    src.add_argument("--slug", type=str, help="Slug to operate on")

    p.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        choices=[
            "lyrics",
            "karaoke",
            "car-karaoke",
            "no-bass",
            "car-bass-karaoke",
        ],
    )

    p.add_argument("--model", type=str, default="htdemucs", help="Demucs model name")
    p.add_argument("--steps", type=str, help="Steps to run, e.g. 24 or 12345")

    p.add_argument(
        "--do",
        type=str,
        choices=["new", "remix", "retime", "mp4"],
        help=(
            "Shortcuts: "
            "new=1+2+3+4, "
            "remix=2+4 (reuse stems), "
            "retime=3+4, "
            "mp4=4 only."
        ),
    )

    p.add_argument(
        "--skip-ui",
        action="store_true",
        help="Non-interactive mode; use config defaults without the Mix UI",
    )

    return p.parse_args(argv)

# ==========================================================
# INTERACTIVE FLOW
# ==========================================================
def interactive_slug_and_steps(args):
    slug = args.slug
    t1   = 0.0

    # Step 1 automatically triggered when query provided without slug
    if args.query and not slug:
        log("MASTER", f'Running step 1 for query "{args.query}"', CYAN)
        slug, t1 = run_step1_txt_mp3(args.query)

    # Infer last slug if still missing
    last_slug = None
    if not slug:
        try:
            last_slug = detect_slug_from_latest_mp3()
        except Exception:
            last_slug = None

    # UI prompt for step 1 query / reuse last slug
    if not slug:
        if last_slug:
            prompt = (
                f'Enter search query for step 1 '
                f'(or ENTER to reuse last slug "{last_slug}"): '
            )
        else:
            prompt = "Enter search query for step 1 (blank = specify slug manually): "

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
                    slug = input("Enter existing slug: ").strip()
                except EOFError:
                    slug = ""
                if not slug:
                    raise SystemExit("Slug is required when no query is given.")

    slug   = slugify(slug)
    status = detect_assets(slug, args.profile)
    print_asset_status(slug, args.profile, status)

    suggested = suggest_steps(status)
    try:
        step_str = input(
            "Steps to run "
            "(1=txt/mp3,2=stems,3=timing,4=mp4,5=upload, "
            f"0=none, ENTER for suggested={suggested}): "
        ).strip()
    except EOFError:
        step_str = ""

    if not step_str:
        step_str = suggested
    if step_str == "0":
        log("MASTER", "Nothing selected; exiting.", YELLOW)
        return slug, [], t1

    steps = parse_steps_string(step_str)

    # If user picked step 1 but did not specify a query yet → prompt here
    if 1 in steps and not args.query:
        try:
            q = input("Step 1 selected. Enter search query: ").strip()
        except EOFError:
            q = ""
        if not q:
            log("MASTER", "Query missing; dropping step 1.", YELLOW)
            steps.remove(1)
        else:
            args.query = q

    return slug, steps, t1


# ==========================================================
# NON-INTERACTIVE FLOW
# ==========================================================
def noninteractive_slug_and_steps(args):
    if not args.steps:
        raise SystemExit("--skip-ui requires --steps (e.g. --steps 24).")

    steps = parse_steps_string(args.steps)
    if not steps:
        return "", []

    slug = args.slug

    # If step 1 selected
    if 1 in steps:
        if not args.query:
            raise SystemExit("Step 1 selected but no --query provided.")
        slug, _ = run_step1_txt_mp3(args.query)

    if not slug:
        raise SystemExit("Slug is required for noninteractive steps 2–5.")

    slug = slugify(slug)
    return slug, steps


# ==========================================================
# MAIN
# ==========================================================
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    total_start = time.perf_counter()

    # =============================================================
    # DO-SHORTCUT HANDLING (Skip interactive menu entirely)
    # =============================================================
    if args.do:
        # Validate required input
        if args.do == "new":
            if not args.query:
                raise SystemExit("--do new requires --query.")
            args.steps = "1234"
            args.skip_ui = True

        elif args.do == "remix":
            if not args.slug:
                raise SystemExit("--do remix requires --slug.")
            args.steps = "24"
            # User chooses UI or not via --skip-ui

        elif args.do == "retime":
            if not args.slug:
                raise SystemExit("--do retime requires --slug.")
            args.steps = "34"
            args.skip_ui = True

        elif args.do == "mp4":
            if not args.slug:
                raise SystemExit("--do mp4 requires --slug.")
            args.steps = "4"
            args.skip_ui = True

        # For any --do value, completely skip interactive menu
        slug = args.slug or slugify(args.query)
        steps = {int(c) for c in str(args.steps)}
        t1 = 0.0
        log("MASTER", f"Running steps {steps} for slug={slug} (do={args.do})", CYAN)
    else:
        # =============================================================
        # NORMAL INTERACTIVE FLOW
        # =============================================================
        if args.skip_ui:
            slug, steps = noninteractive_slug_and_steps(args)
            t1 = 0.0
        else:
            slug, steps, t1 = interactive_slug_and_steps(args)

        if not steps:
            return
        # =============================================================
    # RUN SELECTED STEPS (standard pipeline execution)
    # =============================================================
    t2 = t3 = t4 = t5 = 0.0

    # Step 1
    if 1 in steps and not args.skip_ui and t1 == 0.0:
        if not args.query:
            raise SystemExit("Step 1 requires a query.")
        slug, t1 = run_step1_txt_mp3(args.query)

    # Step 2
    if 2 in steps:
        t2 = run_step2_stems(slug, args.profile, args.model, interactive=not args.skip_ui)

    # Step 3
    if 3 in steps:
        t3 = run_step3_timing(slug)

    # Step 4
    if 4 in steps:
        t4 = run_step4_mp4(slug, args.profile)

    # Step 5
    if 5 in steps:
        t5 = run_step5_upload(slug, args.profile)

    # =============================================================
    # SUMMARY
    # =============================================================
    total_end = time.perf_counter()
    total = total_end - total_start

    print()
    print(f"{BOLD}{BLUE}========= PIPELINE SUMMARY ({slug}, profile={args.profile}) ========={RESET}")
    print(f"{CYAN}Step 1 txt/mp3:  {fmt_secs_mmss(t1)}{RESET}")
    print(f"{CYAN}Step 2 stems:    {fmt_secs_mmss(t2)}{RESET}")
    print(f"{CYAN}Step 3 timing:   {fmt_secs_mmss(t3)}{RESET}")
    print(f"{CYAN}Step 4 mp4:      {fmt_secs_mmss(t4)}{RESET}")
    print(f"{CYAN}Step 5 upload:   {fmt_secs_mmss(t5)}{RESET}")
    print(f"{BOLD}{GREEN}Total pipeline: {fmt_secs_mmss(total)}{RESET}")
    print(f"{BOLD}{BLUE}====================================================={RESET}")

if __name__ == "__main__":
    main()

# end of 0_master.py
