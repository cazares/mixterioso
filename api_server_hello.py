#!/usr/bin/env python3
# api_server_hello.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Mixterioso Hello API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/hello")
def hello():
    return {"status": "ok", "message": "Hello from Mixterioso API"}

# main.py
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI()

class LyricsRequest(BaseModel):
    query: str

@app.post("/lyrics")
def get_lyrics(req: LyricsRequest):
    q = req.query.strip()

    # TODO: replace with real Genius → Musixmatch pipeline
    # TEMP: return mock lyrics to confirm mobile <-> backend flow works
    return {
        "query": q,
        "title": "Californication",
        "artist": "Red Hot Chili Peppers",
        "lyrics_text": (
            "Psychic spies from China\n"
            "Try to steal your mind's elation\n"
            "Little girls from Sweden\n"
            "Dream of silver-screen quotation\n"
        ),
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)


# @app.post("/lyrics")
def get_lyrics2(req: LyricsRequest):
    query = req.query.strip()

    # 1. Genius search
    genius_data = genius_search(query)
    if not genius_data:
        raise HTTPException(404, "Lyrics not found on Genius")

    artist = genius_data["artist"]
    title = genius_data["title"]

    # 2. Musixmatch fetch
    lyrics = musixmatch_get_lyrics(artist, title)
    if not lyrics:
        raise HTTPException(404, "Lyrics not found on Musixmatch")

    # 3. Return
    return {
        "query": query,
        "title": title,
        "artist": artist,
        "lyrics_text": lyrics,
    }


# end of api_server_hello.py
