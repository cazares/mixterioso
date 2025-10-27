#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_lyrics.py — multi-provider lyric fetcher (no API keys)
Now includes colorized + debug logging, per-request byte sizes,
and small inter-thread delays to avoid being rate-limited.
"""

import sys, re, json, time, hashlib, logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
from colorama import Fore, Style, init

# ----- setup -----
init(autoreset=True)
# Real browser UA to avoid blocking
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/118.0.0.0 Safari/537.36")
CACHE_DIR = Path("lyrics_results")
CACHE_DIR.mkdir(exist_ok=True)

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.DEBUG: Fore.WHITE,
    }
    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        prefix = f"{Fore.MAGENTA}{time.strftime('%H:%M:%S')}{Style.RESET_ALL}"
        msg = super().format(record)
        return f"{prefix} {color}{msg}{Style.RESET_ALL}"

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColorFormatter("%(levelname)s %(message)s"))
root = logging.getLogger()
root.setLevel(logging.DEBUG)  # enable full detail
root.handlers = [handler]

# ----- util -----
def safe_name(artist, title, provider):
    base = f"{artist} - {title} - {provider}"
    h = hashlib.sha1(base.encode()).hexdigest()[:8]
    return CACHE_DIR / f"{base} [{h}].txt"

def save_result(artist, title, provider, lyrics):
    p = safe_name(artist, title, provider)
    p.write_text(lyrics, encoding="utf-8")
    logging.info(f"💾 saved → {p.name}")

# ----- provider 1: lyrics.ovh -----
def lyrics_ovh(artist, title):
    name = "lyrics.ovh"
    url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
    logging.info(f"→ Trying {name}")
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            logging.debug(f"{name} HTTP {r.status_code}, {len(r.text)} bytes from {url}")
            if r.status_code == 200 and r.json().get("lyrics"):
                return {"provider": name, "ok": True,
                        "lyrics": r.json()["lyrics"], "url": url}
        except Exception as e:
            logging.warning(f"{name} timeout/err attempt {attempt}: {e}")
        time.sleep(attempt * 2)
    return {"provider": name, "ok": False, "error": "no lyrics"}

# ----- provider 2: Genius scrape -----
def genius_scrape(artist, title):
    name = "genius-scrape"
    q = f"{artist} {title} site:genius.com"
    logging.info(f"→ Trying {name}")
    try:
        search = requests.get("https://duckduckgo.com/html/",
                              params={"q": q}, headers={"User-Agent": UA}, timeout=20)
        logging.debug(f"{name} search resp: {search.status_code}, {len(search.text)} bytes")
        m = re.search(r'https://genius\.com/[^\"]+', search.text)
        if not m:
            logging.debug(f"{name} no Genius link found; first 400 chars:\n{search.text[:400]}")
            return {"provider": name, "ok": False, "error": "no link"}
        url = m.group(0)
        page = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        logging.debug(f"{name} page resp: {page.status_code}, {len(page.text)} bytes from {url}")
        soup = BeautifulSoup(page.text, "html.parser")
        divs = soup.find_all("div", attrs={"data-lyrics-container": "true"})
        if not divs:
            divs = soup.select(".lyrics")
        text = "\n".join(d.get_text('\n') for d in divs).strip()
        if text:
            return {"provider": name, "ok": True, "lyrics": text, "url": url}
    except Exception as e:
        return {"provider": name, "ok": False, "error": str(e)}
    return {"provider": name, "ok": False, "error": "no lyrics"}

# ----- provider 3: Fandom scrape -----
def fandom_scrape(artist, title):
    name = "lyrics.fandom"
    logging.info(f"→ Trying {name}")
    search_url = f"https://lyrics.fandom.com/wiki/{artist.replace(' ', '_')}:{title.replace(' ', '_')}"
    try:
        page = requests.get(search_url, headers={"User-Agent": UA}, timeout=20)
        logging.debug(f"{name} HTTP {page.status_code}, {len(page.text)} bytes from {search_url}")
        soup = BeautifulSoup(page.text, "html.parser")
        box = soup.find("div", {"class": "lyricbox"})
        if box:
            for br in box.find_all(["br", "script"]):
                br.replace_with("\n")
            text = box.get_text("\n").strip()
            if text:
                return {"provider": name, "ok": True, "lyrics": text, "url": search_url}
    except Exception as e:
        return {"provider": name, "ok": False, "error": str(e)}
    return {"provider": name, "ok": False, "error": "no lyrics"}

# ----- provider 4: generic search-engine scrape -----
def search_engine_scrape(artist, title):
    name = "search-engine"
    q = f'{artist} {title} lyrics'
    logging.info(f"→ Trying {name}")
    try:
        r = requests.get("https://duckduckgo.com/html/",
                         params={"q": q}, headers={"User-Agent": UA}, timeout=20)
        logging.debug(f"{name} search HTTP {r.status_code}, {len(r.text)} bytes")
        urls = re.findall(r'https://[^\"]+', r.text)
        seen = set()
        for u in urls:
            if any(bad in u for bad in ["youtube", "spotify", "wikipedia", "duckduckgo"]) or u in seen:
                continue
            seen.add(u)
            try:
                page = requests.get(u, headers={"User-Agent": UA}, timeout=15)
                logging.debug(f"{name} candidate {u} -> {page.status_code}, {len(page.text)} bytes")
                if not page.ok:
                    continue
                soup = BeautifulSoup(page.text, "html.parser")
                txt = soup.get_text(separator="\n")
                if "lyrics" in txt.lower() and len(txt.split()) > 50:
                    snippet = "\n".join(txt.splitlines()[:400])
                    return {"provider": name, "ok": True, "lyrics": snippet, "url": u}
            except Exception as inner:
                logging.debug(f"{name} inner error {inner} for {u}")
                continue
    except Exception as e:
        return {"provider": name, "ok": False, "error": str(e)}
    return {"provider": name, "ok": False, "error": "no lyrics found"}

# ----- provider 5: wikipedia summary search -----
def wikipedia_summary(artist, title):
    name = "wikipedia"
    logging.info(f"→ Trying {name}")
    q = f"{artist} {title} song"
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{q.replace(' ', '_')}"
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        logging.debug(f"{name} HTTP {r.status_code}, {len(r.text)} bytes from {url}")
        if r.status_code == 200:
            data = r.json()
            extract = data.get("extract")
            if extract:
                snippet = extract.strip()
                return {"provider": name, "ok": True, "lyrics": snippet, "url": data.get("content_urls", {}).get("desktop", {}).get("page", url)}
        return {"provider": name, "ok": False, "error": "no summary"}
    except Exception as e:
        return {"provider": name, "ok": False, "error": str(e)}

# ----- main orchestration -----
def fetch_all(artist, title):
    funcs = [lyrics_ovh, genius_scrape, fandom_scrape, search_engine_scrape, wikipedia_summary]
    results = []
    with ThreadPoolExecutor(max_workers=len(funcs)) as ex:
        futs = {}
        for f in funcs:
            logging.debug(f"Scheduling {f.__name__}")
            time.sleep(0.3)
            fut = ex.submit(f, artist, title)
            futs[fut] = f.__name__
        for fut in as_completed(futs):
            res = fut.result()
            results.append(res)
            if res.get("ok"):
                logging.info(Fore.GREEN + f"✅ {res['provider']} success")
                save_result(artist, title, res["provider"], res["lyrics"])
            else:
                logging.warning(f"❌ {res['provider']} failed: {res.get('error')}")
    return results

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/fetch_lyrics.py 'Artist' 'Song'")
        sys.exit(1)
    artist, title = sys.argv[1], sys.argv[2]
    logging.info(f"🎵 Fetching lyrics for: {artist} — {title}")
    out = fetch_all(artist, title)
    ok = [r for r in out if r.get("ok")]
    summary = {
        "artist": artist,
        "title": title,
        "total_sources": len(out),
        "successes": len(ok),
        "providers": [r["provider"] for r in ok],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
# end of fetch_lyrics.py
