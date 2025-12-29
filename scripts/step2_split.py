#!/usr/bin/env python3
"""
Step 2 — split / mix audio

Behavior:
- Default: "full" mix (fast). Ensures mixes/<slug>.mp3 and mixes/<slug>.wav exist and match.
- Optional: "stems" mix (Demucs + ffmpeg mix) when requested via mix_mode or stem level overrides.

Stem levels are expressed as PERCENTAGES, not dB:
- 100 = unchanged
- 0 = muted
- 150 = +50% amplitude
"""

from __future__ import annotations

from pathlib import Path

from .common import (
    IOFlags,
    Paths,
    log,
    run_cmd,
    have_exe,
    write_json,
    WHITE,
    GREEN,
    YELLOW,
)


def _pct_to_gain(pct: float) -> float:
    try:
        return float(pct) / 100.0
    except Exception:
        return 1.0


def _ensure_wav_from_audio(src_audio: Path, out_wav: Path, flags: IOFlags) -> None:
    """
    Ensure mixes/<slug>.wav exists and is not stale relative to src_audio.
    This prevents the renderer (4_mp4.py) from accidentally using an old WAV.
    """
    if out_wav.exists() and not flags.force:
        try:
            if out_wav.stat().st_mtime >= src_audio.stat().st_mtime:
                return
        except Exception:
            pass

    if not have_exe("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH (required to produce mixes/*.wav)")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src_audio),
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    log("MIX", f"Building WAV: {out_wav.name} (from {src_audio.name})", WHITE)
    run_cmd(cmd, tag="FFMPEG", dry_run=flags.dry_run)
    if not flags.dry_run and not out_wav.exists():
        raise RuntimeError(f"Failed to produce {out_wav}")


def _encode_mp3_from_wav(src_wav: Path, out_mp3: Path, flags: IOFlags) -> None:
    if out_mp3.exists() and not flags.force:
        try:
            if out_mp3.stat().st_mtime >= src_wav.stat().st_mtime:
                return
        except Exception:
            pass

    if not have_exe("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH (required to produce mixes/*.mp3)")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src_wav),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(out_mp3),
    ]
    log("MIX", f"Encoding MP3: {out_mp3.name} (from {src_wav.name})", WHITE)
    run_cmd(cmd, tag="FFMPEG", dry_run=flags.dry_run)
    if not flags.dry_run and not out_mp3.exists():
        raise RuntimeError(f"Failed to produce {out_mp3}")


def _ensure_demucs_stems(paths: Paths, slug: str, src_mp3: Path, flags: IOFlags) -> Path:
    """
    Ensure Demucs stems exist and return the stem directory containing vocals/bass/drums/other WAVs.

    Expected layout (Demucs default):
      separated/DEFAULT_DEMUCS_MODEL/<slug>/{vocals,bass,drums,other}.wav
    """
    model = "htdemucs"
    stem_dir = paths.separated / "htdemucs" / slug

    have_all = all((stem_dir / f"{name}.wav").exists() for name in ("vocals", "bass", "drums", "other"))
    if have_all and not flags.force:
        log("SPLIT", f"Using existing stems: {stem_dir}", GREEN)
        return stem_dir

    if not have_exe("demucs"):
        raise RuntimeError("demucs not found on PATH (required for --mix-mode stems or stem level overrides)")

    cmd = [
        "demucs",
        "-n",
        "htdemucs",
        "--shifts", 
        "1", 
        "--overlap", 
        "0.10", 
        "-d", 
        "mps",
        "-o",
        str(paths.separated),
        str(src_mp3),
    ]
    log("SPLIT", f"Running Demucs ({model}) -> {paths.separated}", WHITE)
    run_cmd(cmd, tag="DEMUCS", dry_run=flags.dry_run)

    if flags.dry_run:
        return stem_dir

    # Validate output
    if not stem_dir.exists():
        raise RuntimeError(f"Demucs output directory not found: {stem_dir}")

    missing = [name for name in ("vocals", "bass", "drums", "other") if not (stem_dir / f"{name}.wav").exists()]
    if missing:
        raise RuntimeError(f"Demucs stems missing in {stem_dir}: {missing}")

    return stem_dir


def _mix_stems_to_wav(
    *,
    vocals_wav: Path,
    bass_wav: Path,
    drums_wav: Path,
    other_wav: Path,
    vocals_pct: float,
    bass_pct: float,
    drums_pct: float,
    other_pct: float,
    out_wav: Path,
    flags: IOFlags
) -> None:
    if not have_exe("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH (required for stems mixing)")

    vg = _pct_to_gain(vocals_pct)
    bg = _pct_to_gain(bass_pct)
    dg = _pct_to_gain(drums_pct)
    og = _pct_to_gain(other_pct)

    # Use linear volume factors (percentages), NOT dB.
    fc = (
        f"[0:a]volume={vg}[v];"
        f"[1:a]volume={bg}[b];"
        f"[2:a]volume={dg}[d];"
        f"[3:a]volume={og}[o];"
        f"[v][b][d][o]amix=inputs=4:normalize=0,alimiter=limit=0.98"
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(vocals_wav),
        "-i",
        str(bass_wav),
        "-i",
        str(drums_wav),
        "-i",
        str(other_wav),
        "-filter_complex",
        fc,
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]

    log(
        "MIX",
        f"Stems mix -> {out_wav.name} | vocals={vocals_pct:.0f}% bass={bass_pct:.0f}% drums={drums_pct:.0f}% other={other_pct:.0f}%",
        WHITE,
    )
    run_cmd(cmd, tag="FFMPEG", dry_run=flags.dry_run)

    if not flags.dry_run and not out_wav.exists():
        raise RuntimeError(f"Failed to produce {out_wav}")


def step2_split(
    paths: Paths,
    *,
    slug: str,
    mix_mode: str,
    vocals: float,
    bass: float,
    drums: float,
    other: float,
    flags: IOFlags,
) -> None:
    """
    Produce mixes/<slug>.mp3 and mixes/<slug>.wav.

    mix_mode:
      - "full": copy mp3s/<slug>.mp3 to mixes/<slug>.mp3, then ensure mixes/<slug>.wav matches.
      - "stems": run Demucs (cached) + apply per-stem percentage levels, then ensure mixes/<slug>.mp3 matches.

    Stem level parameters are percentages (100 = unchanged).
    """
    src_mp3 = paths.mp3s / f"{slug}.mp3"
    out_mp3 = paths.mixes / f"{slug}.mp3"
    out_wav = paths.mixes / f"{slug}.wav"

    if not src_mp3.exists():
        raise RuntimeError(f"Missing source MP3: {src_mp3}")

    mix_mode = (mix_mode or "full").strip().lower()

    # If any stem level is not the default (100%), we must use stems mode.
    need_stems = any(abs(float(v) - 100.0) > 1e-6 for v in (vocals, bass, drums, other))
    if need_stems and mix_mode != "stems":
        log("MIX", f"Stem levels requested; switching mix_mode=stems (was {mix_mode})", WHITE)
        mix_mode = "stems"

    if mix_mode not in ("full", "stems"):
        raise ValueError("mix_mode must be one of: full, stems")

    # Ensure output dirs exist (even in dry-run)
    paths.mixes.mkdir(parents=True, exist_ok=True)
    paths.separated.mkdir(parents=True, exist_ok=True)

    if mix_mode == "full":
        if out_mp3.exists() and not flags.force:
            log("SPLIT", f"Using existing mix MP3: {out_mp3}", GREEN)
        else:
            if flags.dry_run:
                log("SPLIT", f"[dry-run] Would copy {src_mp3} -> {out_mp3}", YELLOW)
            else:
                out_mp3.write_bytes(src_mp3.read_bytes())
                log("SPLIT", f"Copied full mix to {out_mp3}", GREEN)

        _ensure_wav_from_audio(out_mp3, out_wav, flags)

        # Record mix metadata for debugging
        write_json(
            paths.mixes / f"{slug}.mix.json",
            {
                "mode": "full",
                "src": str(src_mp3),
                "mix_mp3": str(out_mp3),
                "mix_wav": str(out_wav),
                "levels_percent": {"vocals": 100, "bass": 100, "drums": 100, "other": 100},
            },
            flags,
            label="mix_meta",
        )

        log("SPLIT", "Step 2 complete (full mix guaranteed)", GREEN)
        return

    # stems mode
    stem_dir = _ensure_demucs_stems(paths, slug, src_mp3, flags)

    vocals_wav = stem_dir / "vocals.wav"
    bass_wav = stem_dir / "bass.wav"
    drums_wav = stem_dir / "drums.wav"
    other_wav = stem_dir / "other.wav"

    # Rebuild WAV (stems mix) if forced or missing
    if out_wav.exists() and not flags.force:
        # If metadata exists and matches, we can reuse; otherwise rebuild.
        meta_path = paths.mixes / f"{slug}.mix.json"
        try:
            if meta_path.exists():
                meta = __import__("json").loads(meta_path.read_text(encoding="utf-8"))
                lev = (meta or {}).get("levels_percent", {})
                if (
                    (meta or {}).get("mode") == "stems"
                    and abs(float(lev.get("vocals", 100)) - float(vocals)) < 1e-6
                    and abs(float(lev.get("bass", 100)) - float(bass)) < 1e-6
                    and abs(float(lev.get("drums", 100)) - float(drums)) < 1e-6
                    and abs(float(lev.get("other", 100)) - float(other)) < 1e-6
                    and out_wav.exists()
                ):
                    log("MIX", f"Reusing existing stems mix WAV: {out_wav}", GREEN)
                else:
                    raise RuntimeError("mix settings changed")
            else:
                raise RuntimeError("no meta")
        except Exception:
            _mix_stems_to_wav(
                vocals_wav=vocals_wav,
                bass_wav=bass_wav,
                drums_wav=drums_wav,
                other_wav=other_wav,
                vocals_pct=vocals,
                bass_pct=bass,
                drums_pct=drums,
                other_pct=other,
                out_wav=out_wav,
                flags=flags,
            )
    else:
        _mix_stems_to_wav(
            vocals_wav=vocals_wav,
            bass_wav=bass_wav,
            drums_wav=drums_wav,
            other_wav=other_wav,
            vocals_pct=vocals,
            bass_pct=bass,
            drums_pct=drums,
            other_pct=other,
            out_wav=out_wav,
            flags=flags,
        )

    _encode_mp3_from_wav(out_wav, out_mp3, flags)

    write_json(
        paths.mixes / f"{slug}.mix.json",
        {
            "mode": "stems",
            "src": str(src_mp3),
            "stems_dir": str(stem_dir),
            "mix_mp3": str(out_mp3),
            "mix_wav": str(out_wav),
            "levels_percent": {"vocals": float(vocals), "bass": float(bass), "drums": float(drums), "other": float(other)},
        },
        flags,
        label="mix_meta",
    )

    log("SPLIT", "Step 2 complete (stems mix guaranteed)", GREEN)


# end of step2_split.py
