import os, io, subprocess, logging
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import StreamingResponse
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Mixterioso")

app = FastAPI(title="Mixterioso Lyrics Alignment API")

# Utility: get audio duration (seconds) using ffprobe
def get_audio_duration(file_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"ffprobe duration check failed: {e}")
        return 0.0

# Utility: load lyrics from text file, detect title/artist header
def load_lyrics_lines(lyrics_text_path: str):
    text = open(lyrics_text_path, "r", encoding="utf-8", errors="ignore").read()
    # Split lines and remove any BOM or zero-width chars
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return [], None  # no lyrics
    title_lines = []
    # Detect pattern: [Title], "by", [Artist] at start
    if len(lines) >= 3 and lines[1].lower() == "by":
        title_lines = [lines[0], "by", lines[2]]
        lines = lines[3:]  # remove title/artist lines from lyrics
        # Remove any empty lines that were around the title block
        lines = [ln for ln in lines if ln]
    return lines, title_lines

# Utility: fuzzy alignment of lyrics lines to recognized words with timestamps
def align_lyrics(lyrics_lines, recognized_words):
    """
    lyrics_lines: list of lyric lines (each line string)
    recognized_words: list of tuples (word_str, word_start_time)
    Returns list of (start_time, line_text) aligned.
    """
    aligned = []
    cur_word_index = 0
    total_words = len(recognized_words)
    # Helper to compute edit distance between lyric words and a segment of recognized words
    def edit_distance(words1, words2):
        # Simple dynamic programming for Levenshtein distance (word-level)
        n, m = len(words1), len(words2)
        dp = [[0]*(m+1) for _ in range(n+1)]
        for i in range(1, n+1):
            dp[i][0] = i
        for j in range(1, m+1):
            dp[0][j] = j
        for i in range(1, n+1):
            for j in range(1, m+1):
                cost = 0 if words1[i-1].lower() == words2[j-1].lower() else 1
                dp[i][j] = min(dp[i-1][j] + 1,    # deletion
                               dp[i][j-1] + 1,    # insertion
                               dp[i-1][j-1] + cost)  # substitution
        return dp[n][m]
    # Iterate through each lyric line and find best match segment in recognized words
    for i, line in enumerate(lyrics_lines):
        if not recognized_words:
            break  # no recognized words at all
        words = [w for w in line.split() if w]  # lyric words
        if not words:
            aligned.append((aligned[-1][0] if aligned else 0.0, line))
            continue
        best_idx = None
        best_score = float('inf')
        best_seg_len = len(words)
        start_search = cur_word_index
        # Allow segment length to vary ±2 for fuzzy matching
        seg_len_candidates = list(range(max(1, len(words)-2), len(words)+3))
        # Search from current word index onwards for a potential match
        for idx in range(start_search, total_words):
            # Stop if remaining recognized words too few for even minimal segment
            if idx >= total_words: break
            for seg_len in seg_len_candidates:
                end_idx = idx + seg_len
                if end_idx > total_words:
                    continue
                segment = [rw[0] for rw in recognized_words[idx:end_idx]]
                # Quick check: first and last words should loosely match to consider (to prune bad fits)
                if segment and (segment[0].lower()[0] != words[0].lower()[0]):
                    continue
                # Compute edit distance between lyric line words and this segment
                score = edit_distance(words, segment)
                # Incorporate simple context: if next lyric line's first word matches word after segment
                if i < len(lyrics_lines)-1 and end_idx < total_words:
                    next_lyric_first = lyrics_lines[i+1].split()[0] if lyrics_lines[i+1].split() else ""
                    if next_lyric_first and recognized_words[end_idx][0].lower() == next_lyric_first.lower():
                        score -= 0.5  # bonus for matching next context
                # Bonus if segment aligns exactly at current search position (continuity with previous)
                if idx == start_search:
                    score -= 0.1
                if score < best_score:
                    best_score = score
                    best_idx = idx
                    best_seg_len = seg_len
            # If first word matches perfectly, break early (assuming best found)
            if best_score == 0:
                break
        # Determine start time for this lyric line
        if best_idx is not None:
            # Use the start time of the first recognized word in best segment
            start_time = recognized_words[best_idx][1]
            aligned.append((start_time, line))
            # Advance current word index for next search
            cur_word_index = best_idx + best_seg_len
        else:
            # No match found – use a fallback time estimate
            fallback_time = aligned[-1][0] + 5.0 if aligned else 0.0
            aligned.append((fallback_time, line))
            cur_word_index = start_search  # don't advance
    return aligned

# Utility: apply formatting for repeated lines (add hyphens to repeated lyric lines)
def mark_repeats(aligned_lines):
    output = []
    def normalize(txt):
        import re
        return re.sub(r'[^A-Za-z0-9]', '', txt).lower()
    for idx, (stime, text) in enumerate(aligned_lines):
        if idx > 0:
            prev_text = aligned_lines[idx-1][1]
            # If current line text matches previous line text (ignoring case/punct)
            if normalize(text) == normalize(prev_text) and text != "":
                # Identify longest word in the line
                words = text.split()
                longest_i = max(range(len(words)), key=lambda i: len([c for c in words[i] if c.isalnum()]))
                target = words[longest_i]
                # If target word ends with punctuation, handle separately
                trailing_punct = target[-1] if not target[-1].isalnum() else ''
                core = target[:-1] if trailing_punct else target
                # Remove existing hyphens from core
                core_stripped = core.strip("-")
                # Reconstruct target with hyphens around core
                new_core = f"-{core_stripped}-"
                new_target = new_core + (trailing_punct if trailing_punct else "")
                words[longest_i] = new_target
                text = " ".join(words)
        output.append((stime, text))
    return output

@app.post("/align")
async def align_endpoint(
    audio: UploadFile = File(...), 
    lyrics: UploadFile = File(...), 
    language: str = Form("en")
):
    """Accepts an MP3 audio and a lyrics TXT, returns a CSV of start_time,lyric_line."""
    # Save uploaded files to disk
    audio_path = f"/tmp/{audio.filename}"
    lyrics_path = f"/tmp/{lyrics.filename}"
    with open(audio_path, "wb") as f:
        f.write(await audio.read())
    with open(lyrics_path, "wb") as f:
        f.write(await lyrics.read())
    # Load lyrics and detect title/artist header
    lyrics_lines, title_header = load_lyrics_lines(lyrics_path)
    if not lyrics_lines:
        raise RuntimeError("Lyrics file is empty or invalid.")
    # Determine language code
    lang = language.lower()
    if lang.startswith("en"):
        lang_code = "en"
        ibm_model = "en-US_BroadbandModel"
        google_lang = "en-US"
    elif lang.startswith("es"):
        lang_code = "es"
        ibm_model = "es-ES_BroadbandModel"
        google_lang = "es-ES"
    else:
        lang_code = "en"
        ibm_model = "en-US_BroadbandModel"
        google_lang = "en-US"
    logger.info(f"Processing alignment (language={lang_code}) for audio: {audio.filename}")
    # Step 1: Try local Whisper transcription
    recognized_words = []
    try:
        import whisper
        model = whisper.load_model("base")  # use smaller model for speed; adjust as needed
        logger.info("Transcribing audio with Whisper (local)...")
        result = model.transcribe(audio_path, language=lang_code, word_timestamps=True)
        # Collect word timestamps
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                word = w.get("word", "").strip()
                start = w.get("start", None)
                if word and start is not None:
                    recognized_words.append((word, float(start)))
        if not recognized_words:
            raise Exception("Whisper produced no words")
        logger.info(f"Whisper transcription produced {len(recognized_words)} words.")
    except Exception as e:
        logger.warning(f"Local Whisper failed or not available: {e}")
    # Step 2: If no result, try IBM Watson STT (free tier)
    if not recognized_words:
        api_key = os.getenv("IBM_STT_APIKEY")
        service_url = os.getenv("IBM_STT_URL")
        if api_key and service_url:
            try:
                from ibm_watson import SpeechToTextV1
                from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
                authenticator = IAMAuthenticator(api_key)
                stt_service = SpeechToTextV1(authenticator=authenticator)
                stt_service.set_service_url(service_url)
                logger.info("Transcribing audio with IBM Watson STT (free tier)...")
                with open(audio_path, "rb") as audio_file:
                    ibm_response = stt_service.recognize(
                        audio=audio_file,
                        content_type="audio/mp3",
                        model=ibm_model,
                        timestamps=True,
                        max_alternatives=1
                    ).get_result()
                # Parse IBM response for word timestamps
                for result in ibm_response.get("results", []):
                    for alt in result.get("alternatives", []):
                        if "timestamps" in alt:
                            for ts in alt["timestamps"]:
                                # ts is [word, start_time, end_time]
                                word, start_time = ts[0], ts[1]
                                recognized_words.append((word, float(start_time)))
                if recognized_words:
                    recognized_words.sort(key=lambda x: x[1])  # sort by time
                    logger.info(f"IBM STT transcription produced {len(recognized_words)} words.")
            except Exception as e:
                logger.warning(f"IBM Watson STT failed: {e}")
    # Step 3: If still no result, try Google Cloud STT (paid)
    if not recognized_words:
        try:
            from google.cloud import speech
            # Google Cloud credentials should be set via environment variable
            client = speech.SpeechClient()
            logger.info("Transcribing audio with Google Cloud STT...")
            # Load audio data
            with open(audio_path, "rb") as f:
                audio_data = f.read()
            audio = speech.RecognitionAudio(content=audio_data)
            config = speech.RecognitionConfig(
                language_code=google_lang,
                enable_word_time_offsets=True
            )
            # Use long_running_recognize for longer files
            audio_duration = get_audio_duration(audio_path)
            if audio_duration > 60:
                operation = client.long_running_recognize(config=config, audio=audio)
                response = operation.result(timeout=audio_duration + 30)
            else:
                response = client.recognize(config=config, audio=audio)
            for result in response.results:
                alternative = result.alternatives[0]
                for w in alternative.words:
                    word = w.word
                    start_time = w.start_time.total_seconds() if hasattr(w.start_time, "total_seconds") else w.start_time.seconds + w.start_time.nanos/1e9
                    recognized_words.append((word, float(start_time)))
            if recognized_words:
                recognized_words.sort(key=lambda x: x[1])
                logger.info(f"Google STT produced {len(recognized_words)} words.")
        except Exception as e:
            logger.warning(f"Google STT failed or not configured: {e}")
    # Step 4: If still no result, try OpenAI Whisper API (paid)
    if not recognized_words:
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                import requests
                logger.info("Transcribing audio with OpenAI Whisper API...")
                headers = {"Authorization": f"Bearer {openai_key}"}
                # OpenAI Whisper API accepts multipart form data
                with open(audio_path, "rb") as audio_file:
                    files = {
                        "file": (audio.filename, audio_file, "audio/mpeg"),
                        "model": (None, "whisper-1"),
                        "response_format": (None, "verbose_json"),
                        "language": (None, lang_code)
                    }
                    resp = requests.post("https://api.openai.com/v1/audio/transcriptions", headers=headers, files=files)
                resp.raise_for_status()
                result_json = resp.json()
                # Parse segments for word timestamps if available
                if "segments" in result_json:
                    for seg in result_json["segments"]:
                        if "words" in seg:
                            for w in seg["words"]:
                                word = w.get("word")
                                start = w.get("start")
                                if word and start is not None:
                                    recognized_words.append((word, float(start)))
                        else:
                            # If no word-level detail, fall back to segment level
                            seg_text = seg.get("text", "").strip()
                            seg_start = float(seg.get("start", 0))
                            if seg_text:
                                # Split segment text into words evenly over segment duration
                                dur = float(seg.get("end", 0) - seg.get("start", 0))
                                words = seg_text.split()
                                for idx, w in enumerate(words):
                                    # approximate each word's start within segment
                                    t = seg_start + (dur * idx/len(words)) if dur > 0 else seg_start
                                    recognized_words.append((w, t))
                else:
                    # If API returned only full text, put it at time 0
                    full_text = result_json.get("text", "")
                    for w in full_text.split():
                        recognized_words.append((w, 0.0))
                if recognized_words:
                    recognized_words.sort(key=lambda x: x[1])
                    logger.info(f"OpenAI Whisper API produced {len(recognized_words)} words.")
            except Exception as e:
                logger.error(f"OpenAI Whisper API failed: {e}")
    if not recognized_words:
        raise RuntimeError("No transcription could be obtained from any method.")
    # Align lyrics lines to recognized word timestamps
    aligned = align_lyrics(lyrics_lines, recognized_words)
    # Merge title/artist header lines back (at time 0.0)
    final_aligned = []
    if title_header:
        # If a title block was present in lyrics, prepend it
        title, by_token, artist = title_header[0], title_header[1], title_header[2]
        final_aligned.append((0.0, title))
        final_aligned.append((0.0, by_token))
        final_aligned.append((0.0, artist))
    else:
        logger.info("No title/artist header found in lyrics.")
    final_aligned.extend(aligned)
    # Mark repeated lines with hyphens to indicate repeats
    final_aligned = mark_repeats(final_aligned)
    # Prepare CSV output in-memory
    output_buf = io.StringIO()
    output_buf.write("start_time,lyric_line\n")
    for start_time, line in final_aligned:
        output_buf.write(f"{start_time:.1f},{line}\n")
    output_buf.seek(0)
    # Return as CSV file response
    return StreamingResponse(output_buf, media_type="text/csv", 
                             headers={"Content-Disposition": f"attachment; filename=aligned_{audio.filename}.csv"})
