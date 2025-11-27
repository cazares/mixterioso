# main.py
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests

# load .env file
load_dotenv()

app = FastAPI()

GENIUS_API_KEY = os.getenv("GENIUS_ACCESS_TOKEN")
MUSIXMATCH_API_KEY = os.getenv("MUSIXMATCH_API_KEY")


# ----------------------------------------------------
# REQUEST MODEL
# ----------------------------------------------------
class LyricsRequest(BaseModel):
    query: str


# ----------------------------------------------------
# GENIUS SEARCH → return canonical artist + song title
# ----------------------------------------------------
def genius_search(query: str):
    if GENIUS_API_KEY is None:
        raise HTTPException(500, "GENIUS_API_KEY missing in environment")

    url = "https://api.genius.com/search"
    params = {"q": query}
    headers = {"Authorization": f"Bearer {GENIUS_API_KEY}"}

    r = requests.get(url, params=params, headers=headers, timeout=10)
    if r.status_code != 200:
        raise HTTPException(502, f"Genius error {r.status_code}: {r.text}")

    data = r.json()
    hits = data.get("response", {}).get("hits", [])
    if not hits:
        raise HTTPException(404, f"No Genius matches for query: {query}")

    top = hits[0]["result"]
    return {
        "artist": top["primary_artist"]["name"],
        "title": top["title"],
    }


# ----------------------------------------------------
# MUSIXMATCH LYRICS LOOKUP, given artist + track title
# ----------------------------------------------------
def musixmatch_get_lyrics(artist: str, title: str):
    if MUSIXMATCH_API_KEY is None:
        raise HTTPException(500, "MUSIXMATCH_API_KEY missing in environment")

    base = "https://api.musixmatch.com/ws/1.1/matcher.lyrics.get"
    params = {
        "q_track": title,
        "q_artist": artist,
        "apikey": MUSIXMATCH_API_KEY,
        "format": "json",
    }

    r = requests.get(base, params=params, timeout=10)
    if r.status_code != 200:
        raise HTTPException(502, f"Musixmatch error {r.status_code}: {r.text}")

    payload = r.json()
    message = payload.get("message", {})
    body = message.get("body", {})

    if not body or "lyrics" not in body:
        return None

    lyrics = body["lyrics"].get("lyrics_body", "").strip()
    if not lyrics:
        return None

    # Musixmatch adds a trailing "**** This Lyrics is NOT for Commercial use *****"
    lyrics = lyrics.split("*******")[0].strip()
    return lyrics


# ----------------------------------------------------
# /lyrics POST ENDPOINT (TRUE MIXTERIOSO VERSION)
# ----------------------------------------------------
@app.post("/lyrics")
def fetch_lyrics(req: LyricsRequest):
    query = req.query.strip()

    # 1. Genius search
    g = genius_search(query)
    artist = g["artist"]
    title = g["title"]

    # 2. Musixmatch precise lyrics
    lyrics = musixmatch_get_lyrics(artist, title)
    if not lyrics:
        raise HTTPException(
            404,
            f"Musixmatch has no lyrics for '{title}' by '{artist}'. Failing hard per pipeline rules."
        )

    # 3. Return final payload
    return {
        "query": query,
        "artist": artist,
        "title": title,
        "lyrics_text": lyrics,
    }


# ----------------------------------------------------
# DEV / sanity route
# ----------------------------------------------------
@app.get("/hello")
def hello():
    return {"ok": True, "msg": "Mixterioso backend is live"}
