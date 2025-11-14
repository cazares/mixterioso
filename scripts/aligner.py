import os, subprocess, logging, requests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Aligner")

# Helper: ffprobe to get audio duration
def get_audio_duration(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Could not determine audio duration: {e}")
        return 0.0

# Helper: load lyrics and detect title/artist header
def load_lyrics(lyrics_path):
    with open(lyrics_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() != ""]
    title_header = []
    if len(lines) >= 3 and lines[1].lower() == "by":
        title_header = [lines[0], "by", lines[2]]
        lines = lines[3:]
        lines = [ln for ln in lines if ln]  # remove any additional blank lines
    return lines, title_header

# Helper: word-level edit distance
def word_edit_distance(words1, words2):
    n, m = len(words1), len(words2)
    dp = [[0]*(m+1) for _ in range(n+1)]
    for i in range(1, n+1):
        dp[i][0] = i
    for j in range(1, m+1):
        dp[0][j] = j
    for i in range(1, n+1):
        for j in range(1, m+1):
            cost = 0 if words1[i-1].lower() == words2[j-1].lower() else 1
            dp[i][j] = min(dp[i-1][j] + 1, dp[i][j-1] + 1, dp[i-1][j-1] + cost)
    return dp[n][m]

# Helper: align lyrics lines to recognized words
def align_lines_to_words(lyrics_lines, recognized_words):
    aligned = []
    cur_idx = 0
    total = len(recognized_words)
    for i, line in enumerate(lyrics_lines):
        words = [w for w in line.split() if w]
        if not words:
            # empty line (rare in lyrics if we stripped blanks) - assign same time as previous or 0
            start_t = aligned[-1][0] if aligned else 0.0
            aligned.append((start_t, line))
            continue
        best_time = None
        best_next_idx = cur_idx
        best_score = float('inf')
        # search recognized words from cur_idx forward
        max_search_range = range(cur_idx, min(cur_idx+50, total))  # limit search window to speed up (50 words ahead)
        if i == 0:
            # for first line, search entire range if needed
            max_search_range = range(0, total)
        for idx in max_search_range:
            # if first word initial doesn't match, skip quickly
            if recognized_words[idx][0][0].lower() != words[0][0].lower():
                continue
            # Try segments around the length of the lyric line
            for seg_len in range(len(words)-2, len(words)+3):
                if seg_len < 1:
                    continue
                seg = [rw[0] for rw in recognized_words[idx: idx+seg_len] if idx+seg_len <= total]
                if not seg:
                    continue
                # If last word of lyric line matches last word of segment (to enforce alignment end)
                # or if segment length equals lyric length, consider a full alignment
                score = word_edit_distance(words, seg)
                # Apply small adjustments for context continuity
                if i < len(lyrics_lines)-1 and idx+seg_len < total:
                    # check if next lyric line's first word matches next recognized word
                    next_word = lyrics_lines[i+1].split()[0] if lyrics_lines[i+1].split() else ""
                    if next_word and recognized_words[idx+seg_len][0].lower() == next_word.lower():
                        score -= 0.5
                if score < best_score:
                    best_score = score
                    best_time = recognized_words[idx][1]
                    best_next_idx = idx + seg_len
            if best_score == 0:
                break  # perfect match found
        if best_time is None:
            # No good match found, estimate timing (e.g., halfway to next known or plus few seconds)
            if aligned and i < len(lyrics_lines)-1:
                # guess between last aligned and what we'll align next (if next aligns)
                next_aligned_time = None
                # find next lines that might align by scanning ahead
                for future_idx in range(i+1, len(lyrics_lines)):
                    # find a recognized word that matches a word in future lyric as clue
                    first_w = lyrics_lines[future_idx].split()[0] if lyrics_lines[future_idx].split() else ""
                    if first_w:
                        for rw in recognized_words[cur_idx:]:
                            if rw[0].lower().startswith(first_w.lower()[0]):
                                next_aligned_time = rw[1]
                                break
                    if next_aligned_time:
                        break
                if next_aligned_time:
                    prev_time = aligned[-1][0]
                    best_time = prev_time + (next_aligned_time - prev_time)/2
            if best_time is None:
                # fallback: if first line fails or no context, use last known time + 5s or 0.0
                best_time = aligned[-1][0] + 5.0 if aligned else 0.0
            best_next_idx = cur_idx
        aligned.append((best_time, line))
        cur_idx = best_next_idx if best_next_idx > cur_idx else cur_idx
    return aligned

# Helper: mark repeated lines with hyphens
def mark_repeated_lines(aligned):
    def normalize(txt):
        import re
        return re.sub(r'[^A-Za-z0-9]', '', txt).lower()
    output = []
    for j, (stime, text) in enumerate(aligned):
        if j > 0 and normalize(text) == normalize(aligned[j-1][1]) and text != "":
            parts = text.split()
            # find longest word in parts
            longest_idx = max(range(len(parts)), key=lambda k: len("".join([c for c in parts[k] if c.isalnum()])))
            target = parts[longest_idx]
            trailing = target[-1] if not target[-1].isalnum() else ''
            core = target[:-1] if trailing else target
            core_stripped = core.strip("-")
            parts[longest_idx] = f"-{core_stripped}-" + (trailing if trailing else "")
            text = " ".join(parts)
        output.append((stime, text))
    return output

def transcribe_local_whisper(audio_path, lang_code):
    try:
        import whisper
        model = whisper.load_model("base")
        logger.info("Transcribing with local Whisper...")
        result = model.transcribe(audio_path, language=lang_code, word_timestamps=True)
        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                if "word" in w and "start" in w:
                    word = w["word"].strip()
                    start = float(w["start"])
                    if word:
                        words.append((word, start))
        return words
    except Exception as e:
        logger.warning(f"Local Whisper failed: {e}")
        return []

def transcribe_ibm(audio_path, model):
    api_key = os.getenv("IBM_STT_APIKEY")
    url = os.getenv("IBM_STT_URL")
    if not api_key or not url:
        return []
    try:
        from ibm_watson import SpeechToTextV1
        from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
        authenticator = IAMAuthenticator(api_key)
        stt = SpeechToTextV1(authenticator=authenticator)
        stt.set_service_url(url)
        logger.info("Transcribing with IBM Watson STT...")
        with open(audio_path, "rb") as f:
            response = stt.recognize(audio=f, content_type="audio/mp3",
                                      model=model, timestamps=True).get_result()
        words = []
        for result in response.get("results", []):
            for alt in result.get("alternatives", []):
                for ts in alt.get("timestamps", []):
                    word, start = ts[0], ts[1]
                    words.append((word, float(start)))
        return words
    except Exception as e:
        logger.warning(f"IBM STT failed: {e}")
        return []

def transcribe_google(audio_path, lang_code):
    try:
        from google.cloud import speech
        client = speech.SpeechClient()
        logger.info("Transcribing with Google Cloud STT...")
        with open(audio_path, "rb") as f:
            content = f.read()
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(language_code=lang_code, enable_word_time_offsets=True)
        duration = get_audio_duration(audio_path)
        if duration > 60:
            operation = client.long_running_recognize(config=config, audio=audio)
            response = operation.result(timeout=duration+30)
        else:
            response = client.recognize(config=config, audio=audio)
        words = []
        for result in response.results:
            alt = result.alternatives[0]
            for w in alt.words:
                word = w.word
                start_sec = w.start_time.seconds + w.start_time.nanos*1e-9
                words.append((word, float(start_sec)))
        return words
    except Exception as e:
        logger.warning(f"Google STT failed: {e}")
        return []

def transcribe_openai(audio_path, lang_code):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []
    try:
        logger.info("Transcribing with OpenAI Whisper API...")
        headers = {"Authorization": f"Bearer {api_key}"}
        with open(audio_path, "rb") as f:
            files = {
                "file": (os.path.basename(audio_path), f, "audio/mpeg"),
                "model": (None, "whisper-1"),
                "response_format": (None, "verbose_json"),
                "language": (None, lang_code)
            }
            resp = requests.post("https://api.openai.com/v1/audio/transcriptions", headers=headers, files=files)
        resp.raise_for_status()
        data = resp.json()
        words = []
        if "segments" in data:
            for seg in data["segments"]:
                if "words" in seg:
                    for w in seg["words"]:
                        word = w.get("word")
                        start = w.get("start")
                        if word is not None and start is not None:
                            words.append((word, float(start)))
                else:
                    # no word-level detail; split segment text evenly
                    seg_text = seg.get("text", "")
                    seg_start = float(seg.get("start", 0))
                    seg_end = float(seg.get("end", 0))
                    if seg_text:
                        segment_words = seg_text.split()
                        seg_duration = seg_end - seg_start if seg_end > seg_start else 0
                        for idx, w in enumerate(segment_words):
                            t = seg_start + (seg_duration * idx/len(segment_words)) if seg_duration>0 else seg_start
                            words.append((w, float(t)))
        elif "text" in data:
            # fallback: entire transcript as text with no timing
            t = 0.0
            for w in data["text"].split():
                words.append((w, t))
        return words
    except Exception as e:
        logger.error(f"OpenAI Whisper API failed: {e}")
        return []

def process_alignment(audio_path, lyrics_path, language):
    # Load lyrics and separate title header if present
    lyrics_lines, title_header = load_lyrics(lyrics_path)
    if not lyrics_lines:
        raise ValueError("Lyrics file is empty or invalid.")
    # Determine language codes for services
    lang = language.lower()
    if lang.startswith("es"):
        lang_code = "es"
        ibm_model = "es-ES_BroadbandModel"
        google_lang = "es-ES"
    else:
        lang_code = "en"
        ibm_model = "en-US_BroadbandModel"
        google_lang = "en-US"
    recognized_words = []
    # Fallback transcription sequence
    recognized_words = transcribe_local_whisper(audio_path, lang_code)
    if not recognized_words:
        recognized_words = transcribe_ibm(audio_path, ibm_model)
    if not recognized_words:
        recognized_words = transcribe_google(audio_path, google_lang)
    if not recognized_words:
        recognized_words = transcribe_openai(audio_path, lang_code)
    if not recognized_words:
        raise RuntimeError("Transcription failed with all providers.")
    # Sort recognized words by time
    recognized_words.sort(key=lambda x: x[1])
    # Align lyrics to words
    aligned = align_lines_to_words(lyrics_lines, recognized_words)
    # Prepend title header lines if present (at time 0.0)
    final_lines = []
    if title_header:
        title, by_token, artist = title_header
        final_lines.append((0.0, title))
        final_lines.append((0.0, by_token))
        final_lines.append((0.0, artist))
    final_lines.extend(aligned)
    # Mark repeated lyric lines
    final_lines = mark_repeated_lines(final_lines)
    return final_lines
