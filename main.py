import subprocess
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

class AlignRequest(BaseModel):
    corpus_dir: str              # folder: *.wav + matching *.lab or *.txt
    dictionary_path: str         # .dict file
    acoustic_model_path: str     # .zip or .tar.gz acoustic model
    output_dir: str              # MFA will drop TextGrids here
    extra_args: Optional[List[str]] = []  # raw args, caller fully responsible

@app.post("/align")
def align(req: AlignRequest):
    """
    The caller controls everything:
      - They must guarantee paths exist.
      - They must provide valid MFA models.
      - They must decide any MFA flags.
    This endpoint just runs the command and returns stdout/stderr.
    """

    cmd = [
        "mfa", "align",
        req.corpus_dir,
        req.dictionary_path,
        req.acoustic_model_path,
        req.output_dir,
    ]

    if req.extra_args:
        cmd.extend(req.extra_args)

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "command": cmd,
        }

    return {
        "success": (result.returncode == 0),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": cmd,
    }
