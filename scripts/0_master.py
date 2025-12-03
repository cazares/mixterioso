#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
import os

# Adjust sys.path so `scripts` imports work when running from anywhere
PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT)

# from scripts.mix_utils import load_existing_config  # currently unused

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
# SLUG / META
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
# ASSET STATUS
# ==========================================================
def detect_assets(slug: str, profile: str) -> dict[int, bool]:
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


def print_asset_status(slug: str, profile: str, status: dict[int, bool]) -> None:
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
# STEP RUNNERS
# ==========================================================
def run_step1_txt_mp3(query: str) -> tuple[str, float]:
    if not query:
        raise SystemExit("Step 1 (txt/mp3) requires a non-empty query.")
    t = run([sys.executable, str(SCRIPTS_DIR / "1_txt_mp3.py"), query], "STEP1")
    slug = detect_slug_from_latest_mp3()
    log("STEP1", f"Slug detected: {slug}", GREEN)
    return slug, t


def run_step2_stems(slug: str, profile: str, model: str, interactive: bool) -> float:
    if profile == "lyrics":
        log("STEP2", "Profile 'lyrics' skips stems/mix.", YELLOW)
        return 0.0

    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    if not mp3_path.exists() or not txt_path.exists():
        raise SystemExit(f"Missing assets for step 2: {mp3_path}, {txt_path}")

    separated_root = BASE_DIR / "separated"

    # Preferred models (strict 4-stem)
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

    if existing_model and interactive:
        ans = input(
            f"Stems found for model '{existing_model}'. Reuse existing stems? [Y/n]: "
        ).strip().lower()
        reuse = ans in ("", "y", "yes")
    elif existing_model:
        reuse = True

    t_demucs = 0.0

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

    # Mix UI
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

    # Render mix
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


def run_step5_upload(slug: str, profile: str) -> float:
    artist, title = load_meta(slug)
    artist = artist or "Unknown Artist"
    title  = title or slug.replace("_", " ").title()

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

    mp4_path = OUTPUT_DIR / f"{slug}_{profile}.mp4"
    wav_path = MIXES_DIR / f"{slug}_{profile}.wav"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    def rel(p: Path) -> str:
        try:
            return f"./{p.relative_to(BASE_DIR)}"
        except ValueError:
            return str(p)

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
        abs(v_piano - 1.0) < 1e-3
        and abs(v_other - 1.0) < 1e-3
    )

    suggestions: list[str] = []

    if abs(v_vocals) < 1e-3:
        suggestions.append("Karaoke")

    if all_instr_100 and 0.0 < v_vocals < 1.0:
        label = "Car Karaoke" if "car" in profile else "Karaoke"
        suggestions.append(f"{label}, {int(round(v_vocals * 100))}% Vocals")

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

    if all_instr_100 and abs(v_vocals - 1.0) < 1e-3:
        suggestions.append("Lyrics")
        suggestions.append("Letra")

    if not suggestions:
        suggestions.append(profile.replace("-", " ").title())

    dedup: list[str] = []
    for s in suggestions:
        if s not in dedup:
            dedup.append(s)
    suggestions = dedup

    default_desc = suggestions[0]

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

    print()
    try:
        raw = input(
            f"Enter upload descriptor "
            f"(or choose 1-{len(suggestions)}) "
            f"[default={default_desc}]: "
        ).strip()
    except EOFError:
        raw = ""

    if not raw:
        desc = default_desc
    else:
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
# ARG PARSING
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
# INTERACTIVE / NON-INTERACTIVE SLUG + STEPS
# ==========================================================
def interactive_choose_slug_and_steps(args):
    """
    Option 3 behavior:

    - If steps 1–4 are all missing (fresh pipeline), ask:
        'Run full pipeline 1→4 now? [Y/n]'
      If 'Y' => steps = [1,2,3,4]
      If 'N' => ask for explicit step numbers.

    - Otherwise (some assets present), just ask for explicit steps.
    """
    slug = args.slug.strip() if args.slug else ""
    query = args.query or ""
    t1 = 0.0

    last_slug = None

    # If neither slug nor query provided, ask the classic question
    if not slug and not query:
        try:
            last_slug = detect_slug_from_latest_mp3()
        except Exception:
            last_slug = None

        if last_slug:
            prompt = (
                f'Enter search query for step 1 '
                f'(or ENTER to reuse last slug "{last_slug}"): '
            )
        else:
            prompt = "Enter search query for step 1 (blank = specify existing slug): "

        try:
            ans = input(prompt).strip()
        except EOFError:
            ans = ""

        if ans:
            query = ans
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

    # Normalize slug if present
    if slug:
        slug = slugify(slug)

    # Determine pipeline status (if we have a slug)
    status = None
    fresh_pipeline = False

    if slug:
        status = detect_assets(slug, args.profile)
        print_asset_status(slug, args.profile, status)
        fresh_pipeline = not any(status.get(k, False) for k in (1, 2, 3, 4))
    else:
        # No slug yet but we do have a query ⇒ brand-new pipeline
        fresh_pipeline = True

    # Decide steps
    steps: list[int] = []

    if fresh_pipeline:
        # Ask if user wants full pipeline 1–4
        try:
            ans = input("Run full pipeline 1→4 now? [Y/n]: ").strip().lower()
        except EOFError:
            ans = "y"
        if ans in ("", "y", "yes"):
            steps = [1, 2, 3, 4]
        else:
            try:
                step_str = input(
                    "Enter step numbers to run (e.g. 24 or 13 or 345, 0=none): "
                ).strip()
            except EOFError:
                step_str = ""
            if not step_str or step_str == "0":
                log("MASTER", "Nothing selected; exiting.", YELLOW)
                return slug, query, [], t1
            steps = parse_steps_string(step_str)
    else:
        # Existing assets present; no auto pipeline suggestion
        try:
            step_str = input(
                "Enter step numbers to run (e.g. 24 or 13 or 345, 0=none): "
            ).strip()
        except EOFError:
            step_str = ""
        if not step_str or step_str == "0":
            log("MASTER", "Nothing selected; exiting.", YELLOW)
            return slug, query, [], t1
        steps = parse_steps_string(step_str)

    return slug, query, steps, t1


def noninteractive_slug_and_steps(args):
    if not args.steps:
        raise SystemExit("--skip-ui requires --steps or --do.")

    steps = parse_steps_string(args.steps)
    if not steps:
        return None, "", []

    slug = args.slug.strip() if args.slug else ""
    query = args.query or ""

    # If step 1 is requested, we rely on query and let slug be determined by step 1.
    if 1 in steps:
        if not query:
            raise SystemExit("Step 1 selected in non-interactive mode but no --query provided.")
        slug = ""  # will be set by step 1
    else:
        if not slug:
            raise SystemExit("Steps 2–5 in non-interactive mode require --slug.")
        slug = slugify(slug)

    return slug, query, steps


# ==========================================================
# MAIN
# ==========================================================
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    total_start = time.perf_counter()

    # Shortcuts: --do
    slug: str | None
    query: str
    steps: list[int]
    t1 = t2 = t3 = t4 = t5 = 0.0

    if args.do:
        if args.do == "new":
            if not args.query:
                raise SystemExit("--do new requires --query.")
            query = args.query
            slug = slugify(query)
            steps = [1, 2, 3, 4]
            args.skip_ui = True

        elif args.do == "remix":
            if not args.slug:
                raise SystemExit("--do remix requires --slug.")
            slug = slugify(args.slug)
            query = ""
            steps = [2, 4]

        elif args.do == "retime":
            if not args.slug:
                raise SystemExit("--do retime requires --slug.")
            slug = slugify(args.slug)
            query = ""
            steps = [3, 4]
            args.skip_ui = True

        elif args.do == "mp4":
            if not args.slug:
                raise SystemExit("--do mp4 requires --slug.")
            slug = slugify(args.slug)
            query = ""
            steps = [4]
            args.skip_ui = True

        log("MASTER", f"Running steps {steps} (do={args.do})", CYAN)

    else:
        if args.skip_ui:
            slug, query, steps = noninteractive_slug_and_steps(args)
        else:
            slug, query, steps, t1 = interactive_choose_slug_and_steps(args)

        if not steps:
            return

    # Execute steps in ascending order, no complex auto-advance.
    for step in sorted(set(steps)):
        if step == 1:
            # Ensure we have a query; if missing, prompt once.
            if not query:
                try:
                    q = input("Step 1 (txt/mp3) needs a search query (blank = use slug): ").strip()
                except EOFError:
                    q = ""
                if q:
                    query = q
                else:
                    if not slug:
                        raise SystemExit("Cannot run step 1: no query and no slug.")
                    # Fallback: use slug text as query
                    query = slug

            slug, t1 = run_step1_txt_mp3(query)

        elif step == 2:
            if not slug:
                raise SystemExit("Step 2 requested before slug is known.")
            t2 = run_step2_stems(slug, args.profile, args.model, interactive=not args.skip_ui)

        elif step == 3:
            if not slug:
                raise SystemExit("Step 3 requested before slug is known.")
            t3 = run_step3_timing(slug)

        elif step == 4:
            if not slug:
                raise SystemExit("Step 4 requested before slug is known.")
            t4 = run_step4_mp4(slug, args.profile)

        elif step == 5:
            if not slug:
                raise SystemExit("Step 5 requested before slug is known.")
            t5 = run_step5_upload(slug, args.profile)

    # After explicit steps: offer upload if appropriate and not already run
    if 4 in steps and 5 not in steps and not args.skip_ui and slug:
        status = detect_assets(slug, args.profile)
        if status.get(4, False) and not status.get(5, False):
            try:
                ans = input("Step 4 completed. Run upload (step 5) now? [y/N]: ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("y", "yes"):
                t5 = run_step5_upload(slug, args.profile)

    total_end = time.perf_counter()
    total = total_end - total_start

    print()
    print(f"{BOLD}{BLUE}========= PIPELINE SUMMARY ({slug or 'N/A'}, profile={args.profile}) ========={RESET}")
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
