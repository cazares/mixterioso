
#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PY = sys.executable

def run():
    if len(sys.argv) < 3 or sys.argv[1] != "--query":
        print("Usage: 0_main.py --query <search>")
        sys.exit(1)

    query = sys.argv[2]

    subprocess.run(
        [PY, SCRIPTS_DIR / "1_fetch.py", "--query", query],
        check=True,
    )

if __name__ == "__main__":
    run()
# end of 0_main.py
