from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import StreamingResponse
import logging, os, io
from aligner import process_alignment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Mixterioso")

app = FastAPI(title="Mixterioso Lyrics Alignment API")

@app.post("/align")
async def align_endpoint(
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    language: str = Form("en")
):
    # Save uploads to disk
    audio_path = f"/tmp/{audio.filename}"
    lyrics_path = f"/tmp/{lyrics.filename}"
    with open(audio_path, "wb") as f:
        f.write(await audio.read())
    with open(lyrics_path, "wb") as f:
        f.write(await lyrics.read())
    logger.info(f"Received files: {audio.filename}, {lyrics.filename} (language={language})")
    # Process alignment
    aligned_lines = process_alignment(audio_path, lyrics_path, language)
    # Stream out CSV
    output_buf = io.StringIO()
    output_buf.write("start_time,lyric_line\n")
    for start_time, line in aligned_lines:
        output_buf.write(f"{start_time:.1f},{line}\n")
    output_buf.seek(0)
    return StreamingResponse(output_buf, media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=aligned_{audio.filename}.csv"})
