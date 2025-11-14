pkill -f "uvicorn.*app:app" 2>/dev/null || true
source demucs_env/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
