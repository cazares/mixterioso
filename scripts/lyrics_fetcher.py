import re
import sys
import time
import requests
from bs4 import BeautifulSoup

def get_lyrics(artist: str, title: str) -> str:
    """Fetch lyrics for the given artist and song title, returning plain text lyrics."""
    # Prepare artist and title for URL usage
    artist_clean = artist.strip()
    title_clean = title.strip()
    # Try each source in order
    lyrics_text = None

    # 1. Try Lyrics.ovh API
    api_url = f"https://api.lyrics.ovh/v1/{requests.utils.requote_uri(artist_clean)}/{requests.utils.requote_uri(title_clean)}"
    for attempt in range(3):
        try:
            res = requests.get(api_url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if "lyrics" in data and data["lyrics"]:
                    lyrics_text = data["lyrics"]
                    # lyrics.ovh sometimes returns \n\n for new lines, keep as is.
                    break
        except Exception as e:
            # You could log the exception if needed for debugging
            pass
        time.sleep(1)  # small delay before retry
    if lyrics_text:
        # Clean and return (lyrics.ovh text is usually clean, just ensure it's ascii)
        return lyrics_text.strip()

    # 2. Try AZLyrics scraping
    # Construct AZLyrics URL (all lowercase, remove non-alphanumeric)
    artist_slug = re.sub(r'[^A-Za-z0-9]', '', artist_clean).lower()
    title_slug = re.sub(r'[^A-Za-z0-9]', '', title_clean).lower()
    az_url = f"https://www.azlyrics.com/lyrics/{artist_slug}/{title_slug}.html"
    headers = {"User-Agent": "Mozilla/5.0"}  # user-agent in case needed
    for attempt in range(3):
        try:
            res = requests.get(az_url, headers=headers, timeout=5)
            if res.status_code == 200:
                html = res.text
                soup = BeautifulSoup(html, "html.parser")
                # Find the special comment that precedes the lyrics
                comment = soup.find(string=lambda text: text and "Usage of azlyrics.com content" in text)
                if comment:
                    lyrics_div = comment.find_next("div")
                else:
                    # Fallback: find all divs with no class that could be lyrics
                    divs = soup.find_all("div")
                    lyrics_div = None
                    for div in divs:
                        if div.find("br"):  # lyrics div typically contains <br> for line breaks
                            lyrics_div = div
                            break
                if lyrics_div:
                    # Replace <br> tags with newline and get text
                    for br in lyrics_div.find_all("br"):
                        br.replace_with("\n")
                    raw_lyrics = lyrics_div.get_text(separator="\n")
                    # Remove any HTML tags remnants and trim
                    lyrics_text = raw_lyrics.strip()
                    if lyrics_text:
                        break
        except Exception:
            pass
        time.sleep(1)
    if lyrics_text:
        # AZLyrics often includes no extra labels, but ensure ASCII only:
        lyrics_text = lyrics_text.encode("ascii", errors="ignore").decode("ascii")
        return lyrics_text

    # 3. Try Genius scraping
    # Construct Genius URL (hyphenate artist and title)
    artist_slug = re.sub(r'\W+', '-', artist_clean.strip())
    title_slug = re.sub(r'\W+', '-', title_clean.strip())
    genius_url = f"https://genius.com/{artist_slug}-{title_slug}-lyrics"
    for attempt in range(3):
        try:
            res = requests.get(genius_url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.content, "html.parser")
                # Select all lyric containers
                containers = soup.select("[data-lyrics-container]")
                if containers:
                    lines = []
                    for c in containers:
                        # Extract text lines from each container
                        for seg in c.stripped_strings:
                            lines.append(seg)
                    raw_lyrics = "\n".join(lines)
                    if raw_lyrics:
                        lyrics_text = raw_lyrics
                        break
        except Exception:
            pass
        time.sleep(1)
    if lyrics_text:
        # Remove bracketed annotations like [Chorus] or (Verse)
        lyrics_text = re.sub(r'[\(\[].*?[\)\]]', '', lyrics_text)
        lyrics_text = lyrics_text.strip()
        # Ensure ASCII-only text output
        lyrics_text = lyrics_text.encode("ascii", errors="ignore").decode("ascii")
        return lyrics_text

    # If all sources failed
    return "Lyrics not found."

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python lyrics_fetcher.py <Artist Name> <Song Title>")
        sys.exit(1)
    artist = sys.argv[1]
    title = " ".join(sys.argv[2:])  # allow song title to have spaces
    result = get_lyrics(artist, title)
    print(result)
