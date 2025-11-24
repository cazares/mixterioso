# scripts/mix_utils.py

import json
from pathlib import Path

# MIXES_DIR should match what 0_master.py and 2_stems.py use
BASE_DIR = Path(__file__).resolve().parent.parent
MIXES_DIR = BASE_DIR / "mixes"


def load_existing_config(slug: str, profile: str):
    """
    Shared helper for loading mix config.
    Mirrors the logic used in 2_stems.py and 0_master.py.
    """
    MIXES_DIR.mkdir(parents=True, exist_ok=True)

    new_path = MIXES_DIR / f"{slug}_{profile}.json"
    old_path = MIXES_DIR / f"{slug}.json"

    path = None
    if new_path.exists():
        path = new_path
    elif old_path.exists():
        path = old_path

    if not path:
        return None, None

    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        vols = cfg.get("volumes", {})
        if isinstance(vols, dict):
            return vols, path
    except Exception:
        pass

    return None, path


def save_config(slug: str, profile: str, model: str, volumes: dict) -> Path:
    """
    Shared helper for saving mix volumes + metadata.
    """
    MIXES_DIR.mkdir(parents=True, exist_ok=True)

    path = MIXES_DIR / f"{slug}_{profile}.json"
    cfg = {
        "slug": slug,
        "profile": profile,
        "model": model,
        "volumes": volumes,
    }

    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path
