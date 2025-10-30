# install Python 3.11 via Homebrew
brew install python@3.11

# make a clean venv
/opt/homebrew/bin/python3.11 -m venv align_env
source align_env/bin/activate
pip3 install --upgrade pip

# CPU-only torch + torchaudio matching versions
pip3 install "torch==2.4.1" "torchaudio==2.4.1" --index-url https://download.pytorch.org/whl/cpu

# asr + align
pip3 install "faster-whisper==1.0.3" "whisperx==3.1.1" pandas

# run
python3 scripts/align_scars.py
