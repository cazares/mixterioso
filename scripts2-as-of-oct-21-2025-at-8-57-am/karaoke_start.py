#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_start.py — fully automated bootstrap & restart-safe launcher
Creates or resets 'demucs_env', installs dependencies, purges caches,
and runs karaoke_generator.py. User never has to manage environments.
"""

import os, sys, subprocess, shutil, time

VENV_DIR = "demucs_env"
REQUIREMENTS = "requirements.txt"

def run(cmd, **kwargs):
    print("▶️", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)

def ensure_requirements():
    """Ensure requirements.txt exists and is ready."""
    if not os.path.exists(REQUIREMENTS):
        print(f"⚠️ {REQUIREMENTS} not found, creating default one.")
        with open(REQUIREMENTS, "w") as f:
            f.write("""requests
soundfile
demucs
torch
torchaudio
ffmpeg-python
tqdm
yt-dlp
""")
    print(f"✅ Requirements file ready: {REQUIREMENTS}")

def purge_pycache():
    """Delete all __pycache__ directories recursively."""
    print("🧹 Clearing stale Python caches...")
    for root, dirs, _ in os.walk("."):
        if "__pycache__" in dirs:
            full = os.path.join(root, "__pycache__")
            try:
                shutil.rmtree(full)
                print(f"  🗑️  Removed: {full}")
            except Exception:
                pass
    print("✅ Caches cleared.\n")

def rebuild_env():
    """Create a fresh Python virtual environment and install dependencies."""
    if os.path.isdir(VENV_DIR):
        print(f"🧹 Removing old virtual environment: {VENV_DIR}")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    print(f"🧱 Creating fresh virtual environment: {VENV_DIR}")
    run([sys.executable, "-m", "venv", VENV_DIR])

    pip_exe = os.path.join(VENV_DIR, "bin", "pip")
    print("📦 Installing dependencies...")
    run([pip_exe, "install", "-U", "pip", "wheel", "setuptools"])
    run([pip_exe, "install", "-r", REQUIREMENTS])

def main():
    purge_pycache()

    inside_venv = (
        sys.prefix != sys.base_prefix and
        os.path.basename(sys.prefix) == VENV_DIR
    )

    if inside_venv:
        print("⚠️ Detected running inside environment being rebuilt.")
        print("🔄 Relaunching from system Python…\n")
        envless_python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"
        cmd = [envless_python, __file__] + sys.argv[1:]
        os.execvp(cmd[0], cmd)
        return

    ensure_requirements()
    rebuild_env()

    py_exe = os.path.join(VENV_DIR, "bin", "python")

    if len(sys.argv) > 1:
        print("\n🎶 Running karaoke_generator.py with your arguments…\n")
        run([py_exe, "karaoke_generator.py"] + sys.argv[1:])
    else:
        print(f"\n✅ Environment ready!")
        print(f"Run manually:\n   python3 karaoke_start.py \"your_song.mp3\" --artist \"Artist\" --title \"Song Title\" --strip-vocals")

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Command failed: {e}")
        sys.exit(1)
