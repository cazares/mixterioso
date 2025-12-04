import subprocess
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

class AlignRequest(BaseModel):
    corpus_dir: str
    dictionary_path: str
    acoustic_model_path: str
    output_dir: str
    extra_args: Optional[List[str]] = []

@app.post("/align")
def align(req: AlignRequest):
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
        return {"success": False, "error": str(e), "command": cmd}

    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": cmd,
    }
