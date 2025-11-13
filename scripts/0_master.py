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
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"
SEPARATED_DIR = BASE_DIR / "separated"

DEMUCS_MODULE = [sys.executable, "-m", "demucs"]  # safer on MacinCloud/venvs
MODEL_6 = "htdemucs_6s"
MODEL_4 = "htdemucs"  # also used as base for --two-stems vocals

STEMS_6 = {"vocals", "drums", "bass", "guitar", "piano", "other"}
STEMS_4 = {"vocals", "drums", "bass", "other"}
STEMS_2 = {"vocals", "no_vocals"}


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


def run_capture(cmd: list[str], section: str) -> tuple[float, str]:
    """
    Like run(), but captures stdout for JSON emitted by scripts/5_upload.py.
    """
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
    t1 = time.perf_counter()
    out = (cp.stdout or "").strip()
    if cp.stderr:
        log(section, f"(stderr) {cp.stderr.strip()}", YELLOW)
    return (t1 - t0), out


def run_capture_no_throw(cmd: list[str], section: str) -> tuple[float, int, str, str]:
    """
    Non-throwing variant: returns (elapsed_secs, returncode, stdout, stderr).
    Ensures we can write an upload receipt even if the child process fails.
    """
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    cp = subprocess.run(cmd, check=False, capture_output=True, text=True)
    t1 = time.perf_counter()
    if cp.stderr:
        log(section, f"(stderr) {cp.stderr.strip()}", YELLOW)
    return (t1 - t0), cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def offset_tag(val: float) -> str:
    """
    Safe suffix for filenames, e.g.
      +0.000  -> _offset_p0p000s
      -1.500  -> _offset_m1p500s
    """
    s = f"{val:+.3f}".replace("-", "m").replace("+", "p").replace(".", "p")
    return f"_offset_{s}s"


def detect_slug_from_latest_mp3() -> str:
    """
    Infer the most recent slug produced by step 1.

    Prefer the newest meta/*.json (always rewritten by 1_txt_mp3),
    and fall back to the newest mp3 if no meta files exist.
    """
    metas = sorted(META_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if metas:
        slug = slugify(metas[-1].stem)
        log("MASTER", f"Latest slug inferred from meta/: {slug}", CYAN)
        return slug

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found in mp3s/ after 1_txt_mp3.")
    slug = slugify(mp3s[-1].stem)
    log("MASTER", f"Latest slug inferred from mp3s/: {slug}", CYAN)
    return slug


def load_meta(slug: str):
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return None, None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data.get("artist"), data.get("title")
    except Exception:
        return None, None


def detect_assets(slug: str, profile: str, offset: float) -> dict:
    txt = TXT_DIR / f"{slug}.txt"
    mp3 = MP3_DIR / f"{slug}.mp3"
    meta = META_DIR / f"{slug}.json"
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    timing_csv = TIMINGS_DIR / f"{slug}.csv"
    mp4 = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset)}.mp4"
    uploaded_flag = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset)}.uploaded.json"

    step1_done = txt.exists() and mp3.exists() and meta.exists()
    if profile == "lyrics":
        step2_done = True
    else:
        step2_done = mix_wav.exists()
    step3_done = timing_csv.exists()
    step4_done = mp4.exists()
    step5_done = uploaded_flag.exists()

    return {
        1: step1_done,
        2: step2_done,
        3: step3_done,
        4: step4_done,
        5: step5_done,
    }


def print_asset_status(slug: str, profile: str, status: dict) -> None:
    print()
    print(f"{BOLD}Pipeline status for slug={slug}, profile={profile}{RESET}")
    labels = {
        1: "txt+mp3 generation (1_txt_mp3)",
        2: "stems/mix (Demucs + mix UI)",
        3: "timings CSV (3_timing)",
        4: "mp4 generation (4_mp4)",
        5: "YouTube upload (5_upload)",
    }
    for step in range(1, 6):
        s = "DONE" if status.get(step) else "MISSING"
        color = GREEN if status.get(step) else YELLOW
        print(f"{color}[{step}] {labels[step]}  -> {s}{RESET}")
    print()


def suggest_steps(status: dict) -> str:
    missing = "".join(str(k) for k in range(1, 6) if not status.get(k))
    return missing or "0"


def parse_steps_string(s: str) -> list[int]:
    steps: list[int] = []
    for ch in s:
        if ch in "012345":
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


def profile_is_vocals_only(profile: str) -> bool:
    """
    True when only vocals are modified by the chosen profile.
    Adjust if your 2_stems.py profiles change.
    """
    # Heuristics: 'karaoke' and 'car-karaoke' mute/reduce vocals only.
    return profile in {"karaoke", "car-karaoke"}


def _model_track_dir(model: str, slug: str) -> Path:
    return SEPARATED_DIR / model / slug


def _stems_present(track_dir: Path, expected: set[str]) -> bool:
    if not track_dir.exists():
        return False
    names = {p.stem for p in track_dir.glob("*.wav")}
    return expected.issubset(names)


def _ensure_two_stems(slug: str, mp3_path: Path) -> float:
    """Run 2-stem (vocals vs accompaniment) using htdemucs base model."""
    t = 0.0
    track_dir = _model_track_dir(MODEL_4, slug)
    if _stems_present(track_dir, STEMS_2):
        log("STEP2", f"Reusing 2-stem at {track_dir}", CYAN)
        return 0.0
    log("STEP2", "Running 2-stem separation (vocals vs accompaniment)", CYAN)
    t += run(DEMUCS_MODULE + ["--two-stems", "vocals", "-n", MODEL_4, str(mp3_path)], "STEP2-2STEM")
    if not _stems_present(track_dir, STEMS_2):
        raise SystemExit("2-stem separation finished but expected stems not found.")
    return t


def _ensure_six_then_four(slug: str, mp3_path: Path) -> tuple[str, float]:
    """Try htdemucs_6s first; on failure/incomplete, fallback to htdemucs (4-stem)."""
    t = 0.0
    d6 = _model_track_dir(MODEL_6, slug)
    d4 = _model_track_dir(MODEL_4, slug)

    if _stems_present(d6, STEMS_6):
        log("STEP2", f"Reusing 6-stem at {d6}", CYAN)
        return MODEL_6, 0.0

    if _stems_present(d4, STEMS_4):
        log("STEP2", f"Reusing 4-stem at {d4}", CYAN)
        return MODEL_4, 0.0

    # Try 6-stem fresh
    try:
        log("STEP2", "Running 6-stem separation (htdemucs_6s)", CYAN)
        t += run(DEMUCS_MODULE + ["-n", MODEL_6, str(mp3_path)], "STEP2-6STEM")
        if _stems_present(d6, STEMS_6):
            return MODEL_6, t
        log("STEP2", "6-stem finished but stems incomplete; falling back to 4-stem.", YELLOW)
    except subprocess.CalledProcessError:
        log("STEP2", "6-stem failed; falling back to 4-stem.", YELLOW)

    # Fallback: 4-stem
    log("STEP2", "Running 4-stem separation (htdemucs)", CYAN)
    t += run(DEMUCS_MODULE + ["-n", MODEL_4, str(mp3_path)], "STEP2-4STEM")
    if not _stems_present(d4, STEMS_4):
        raise SystemExit("4-stem separation finished but expected stems not found.")
    return MODEL_4, t


def run_step2_stems(slug: str, profile: str, model: str, interactive: bool) -> float:
    if profile == "lyrics":
        log("STEP2", "Profile 'lyrics' selected, skipping stems/mix.", YELLOW)
        return 0.0

    mp3_path = MP3_DIR / f"{slug}.mp3"
    txt_path = TXT_DIR / f"{slug}.txt"
    if not mp3_path.exists() or not txt_path.exists():
        raise SystemExit(f"Missing assets for step 2: {mp3_path} or {txt_path}.")

    SEPARATED_DIR.mkdir(parents=True, exist_ok=True)
    t_demucs = 0.0
    actual_model = None
    use_two_stem = profile_is_vocals_only(profile)

    # Reuse prompt (only if interactive and any stems exist)
    any_existing = (
        _stems_present(_model_track_dir(MODEL_6, slug), STEMS_6)
        or _stems_present(_model_track_dir(MODEL_4, slug), STEMS_4)
        or _stems_present(_model_track_dir(MODEL_4, slug), STEMS_2)
    )
    if interactive and any_existing:
        try:
            ans = input("Stems exist for this slug. Reuse and skip separation? [Y/n]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans in ("", "y", "yes"):
            log("STEP2", "Reusing existing stems.", CYAN)
            # Prefer 6 > 4 > 2 for mixing model tag
            if _stems_present(_model_track_dir(MODEL_6, slug), STEMS_6):
                actual_model = MODEL_6
            elif _stems_present(_model_track_dir(MODEL_4, slug), STEMS_4):
                actual_model = MODEL_4
            else:
                actual_model = MODEL_4  # two-stem lives under htdemucs folder
        else:
            log("STEP2", "Will regenerate stems.", YELLOW)

    if actual_model is None:
        if use_two_stem:
            t_demucs += _ensure_two_stems(slug, mp3_path)
            actual_model = MODEL_4  # folder tag; two-stem outputs under htdemucs
        else:
            actual_model, t_added = _ensure_six_then_four(slug, mp3_path)
            t_demucs += t_added

    log("STEP2", f"Mixing with model folder '{actual_model}' (two-stem={use_two_stem})", GREEN)

    # Launch UI to set per-stem gains according to profile, then render
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


def run_step4_mp4(slug: str, profile: str, offset: float) -> float:
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
    try:
        t = run(cmd, "STEP4")
        return t
    except subprocess.CalledProcessError as e:
        log(
            "STEP4",
            f"Step 4 failed (exit {e.returncode}). "
            "Most likely the mixed WAV for this slug/profile is missing. "
            "Run step 2 (stems/mix) first.",
            RED,
        )
        return 0.0


def run_step5_upload(
    slug: str,
    profile: str,
    offset: float,
    title: str | None,
    privacy: str | None,
    tags_csv: str | None,
    made_for_kids: bool,
    thumb_from_sec: float | None,
    no_thumbnail: bool,
) -> float:
    """
    Upload the offset-specific MP4 using scripts/5_upload.py and persist a simple
    upload receipt to OUTPUT_DIR/<slug>_<profile>_offset_*.uploaded.json

    This variant never raises, so master can summarize gracefully.
    """
    mp4 = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset)}.mp4"
    if not mp4.exists():
        log("STEP5", f"Target MP4 not found for upload: {mp4.name}", RED)
        return 0.0

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "5_upload.py"),
        "--file",
        str(mp4),
    ]
    if title:
        cmd += ["--title", title]
    if privacy:
        cmd += ["--privacy", privacy]
    if tags_csv:
        cmd += ["--tags", tags_csv]
    if made_for_kids:
        cmd += ["--made-for-kids"]
    if no_thumbnail:
        cmd += ["--no-thumbnail"]
    if thumb_from_sec is not None:
        cmd += ["--thumb-from-sec", str(thumb_from_sec)]

    t, rc, out, err = run_capture_no_throw(cmd, "STEP5")
    receipt_path = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset)}.uploaded.json"
    payload = {
        "invoked": "5_upload.py",
        "args": cmd,
        "returncode": rc,
        "stdout": out,
        "stderr": err,
        "ts": time.time(),
    }
    try:
        parsed = json.loads(out) if out else {}
        if isinstance(parsed, dict):
            payload.update(parsed)
    except json.JSONDecodeError:
        pass
    receipt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if rc != 0:
        log("STEP5", "Upload failed. See receipt JSON for details.", RED)
        if "ModuleNotFoundError" in err or "No module named" in err:
            log(
                "STEP5",
                "Missing YouTube client libraries. Install in your venv:\n"
                "  pip3 install --upgrade google-api-python-client google-auth-oauthlib google-auth-httplib2",
                YELLOW,
            )
        if "Missing OAuth client secrets" in out or "Missing OAuth client secrets" in err:
            log(
                "STEP5",
                "Provide OAuth client JSON and set env:\n"
                "  export YOUTUBE_CLIENT_SECRETS_JSON=client_secret.json\n"
                "(API keys alone cannot upload; OAuth is required.)",
                YELLOW,
            )
    else:
        log("STEP5", "Upload completed successfully.", GREEN)

    return t


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Karaoke pipeline master (1=txt/mp3, 2=stems, 3=timing, 4=mp4, 5=upload)."
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
    p.add_argument("--model", type=str, default=MODEL_6, help="Demucs model name (default tries 6→4)")
    p.add_argument("--steps", type=str, help="Steps to run, e.g. 24 or 12345")
    p.add_argument(
        "--do",
        type=str,
        choices=["new", "remix", "retime", "mp4", "publish", "render_upload"],
        help=(
            "Shortcut: new=1+2+3+4 from query; remix=2+4; retime=3+4; mp4=4; "
            "publish=5 (upload only); render_upload=4+5."
        ),
    )
    p.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Global lyrics/text offset in seconds. Negative=sooner, Positive=later (baseline=0).",
    )
    p.add_argument("--skip-ui", action="store_true", help="Non-interactive; use --steps / --do as-is")

    # Optional upload arguments (forwarded to 5_upload.py)
    p.add_argument("--upload-title", type=str, help="YouTube title override")
    p.add_argument("--upload-privacy", type=str, choices=["public", "unlisted", "private"], help="YouTube privacy")
    p.add_argument("--upload-tags", type=str, help="Comma-separated tags")
    p.add_argument("--upload-made-for-kids", action="store_true", help="Mark as made for kids")
    p.add_argument("--upload-no-thumbnail", action="store_true", help="Skip setting a thumbnail")
    p.add_argument("--upload-thumb-from-sec", type=float, help="Capture thumbnail at this second (e.g., 0.5)")
    return p.parse_args(argv)


def interactive_slug_and_steps(args):
    slug = args.slug
    t1 = 0.0

    # If query is given and no slug, run step 1 first
    if args.query and not slug:
        log("MASTER", f'Running step 1 (1_txt_mp3) for query "{args.query}"', CYAN)
        slug, t1 = run_step1_txt_mp3(args.query)

    # If we still don't have a slug, try to infer last slug from latest meta/mp3
    last_slug = None
    if not slug:
        try:
            last_slug = detect_slug_from_latest_mp3()
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
    status = detect_assets(slug, args.profile, args.offset)
    print_asset_status(slug, args.profile, status)

    suggested = suggest_steps(status)
    try:
        s = input(
            f"Steps to run (1=txt/mp3,2=stems,3=timing,4=mp4,5=upload, 0=none, ENTER for suggested={suggested}): "
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
        raise SystemExit("--skip-ui requires --steps like 24 or 12345.")
    steps = parse_steps_string(args.steps)
    if not steps:
        return "", []

    slug = args.slug
    if 1 in steps:
        if not args.query:
            raise SystemExit("Step 1 selected but no --query given.")
        slug, _ = run_step1_txt_mp3(args.query)

    if not slug:
        raise SystemExit("Slug is required for steps 2–5 (use --slug or include step 1 with --query).")

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
        elif args.do == "publish":
            if not args.slug:
                raise SystemExit("--do publish requires --slug.")
            args.steps = "5"
        elif args.do == "render_upload":
            if not args.slug:
                raise SystemExit("--do render_upload requires --slug.")
            args.steps = "45"

        # When using --do, run non-interactively by default.
        args.skip_ui = True

    if args.skip_ui:
        slug, steps = noninteractive_slug_and_steps(args)
        t1 = 0.0
    else:
        slug, steps, t1 = interactive_slug_and_steps(args)

    if not steps:
        return

    log(
        "MASTER",
        f"Running steps {steps} for slug={slug}, profile={args.profile} (offset {args.offset:+.3f}s)",
        CYAN,
    )

    t2 = t3 = t4 = t5 = 0.0

    if 1 in steps and not args.skip_ui and t1 == 0.0:
        if not args.query:
            raise SystemExit("Interactive step 1 requires a query.")
        slug, t1 = run_step1_txt_mp3(args.query)

    if 2 in steps:
        t2 = run_step2_stems(slug, args.profile, args.model, interactive=not args.skip_ui)

    if 3 in steps:
        t3 = run_step3_timing(slug)

    if 4 in steps:
        t4 = run_step4_mp4(slug, args.profile, args.offset)

    if 5 in steps:
        t5 = run_step5_upload(
            slug=slug,
            profile=args.profile,
            offset=args.offset,
            title=args.upload_title,
            privacy=args.upload_privacy,
            tags_csv=args.upload_tags,
            made_for_kids=bool(args.upload_made_for_kids),
            thumb_from_sec=args.upload_thumb_from_sec,
            no_thumbnail=bool(args.upload_no_thumbnail),
        )

    total_end = time.perf_counter()
    total = total_end - total_start

    print()
    print(
        f"{BOLD}{BLUE}========= PIPELINE SUMMARY "
        f"({slug}, profile={args.profile}, offset={args.offset:+.3f}s) ========={RESET}"
    )
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
