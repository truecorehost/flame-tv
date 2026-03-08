import requests
import sqlite3
import json
from datetime import datetime, timedelta
from config import TVDB_API_KEY, TVDB_BASE_URL, DB_PATH

_token = None
_token_expiry = None

def get_token():
    global _token, _token_expiry
    if _token and _token_expiry and datetime.now() < _token_expiry:
        return _token
    resp = requests.post(f"{TVDB_BASE_URL}/login", json={"apikey": TVDB_API_KEY})
    resp.raise_for_status()
    _token = resp.json()["data"]["token"]
    _token_expiry = datetime.now() + timedelta(hours=23)
    return _token

def headers():
    return {"Authorization": f"Bearer {get_token()}"}

def search_show(query):
    """Search TVDB for a show by name, returns list of matches"""
    resp = requests.get(f"{TVDB_BASE_URL}/search", params={"query": query, "type": "series"}, headers=headers())
    resp.raise_for_status()
    results = []
    for r in resp.json().get("data", []):
        results.append({
            "tvdb_id": r.get("tvdb_id") or r.get("id"),
            "title": r.get("name"),
            "year": r.get("year"),
            "overview": r.get("overview", "")[:200],
            "status": r.get("status", {}).get("name") if isinstance(r.get("status"), dict) else r.get("status")
        })
    return results

def get_show_details(tvdb_id):
    """Get full show details from TVDB"""
    resp = requests.get(f"{TVDB_BASE_URL}/series/{tvdb_id}/extended", headers=headers())
    resp.raise_for_status()
    return resp.json().get("data", {})

def get_episodes(tvdb_id, season=None):
    """Get all episodes for a show, optionally filtered by season"""
    episodes = []
    page = 0
    while True:
        params = {"page": page}
        if season is not None:
            params["season"] = season
        resp = requests.get(f"{TVDB_BASE_URL}/series/{tvdb_id}/episodes/official", params=params, headers=headers())
        resp.raise_for_status()
        data = resp.json().get("data", {})
        eps = data.get("episodes", [])
        if not eps:
            break
        episodes.extend(eps)
        if len(eps) < 100:
            break
        page += 1
    return episodes

def cache_show(tvdb_id):
    """Fetch show + all episodes from TVDB and store in SQLite. Returns show DB id."""
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    details = get_show_details(tvdb_id)
    title = details.get("name", "Unknown")
    year = details.get("year")
    imdb_id = None
    for remote in details.get("remoteIds", []):
        if remote.get("sourceName") == "IMDB":
            imdb_id = remote.get("id")
            break
    status = details.get("status", {}).get("name") if isinstance(details.get("status"), dict) else details.get("status")

    # Grab poster — type 2 = portrait poster, type 1 = banner (landscape), type 3 = background
    # Prefer the highest-scored type 2 (portrait poster), fall back to series image field
    poster_url = None
    best_score = -1
    for art in details.get("artworks", []):
        if art.get("type") == 2:  # portrait poster
            score = art.get("score", 0) or 0
            if score > best_score:
                best_score = score
                poster_url = art.get("image")
    if not poster_url:
        # Fall back to series image — may be landscape but better than nothing
        poster_url = details.get("image")

    c.execute("""
        INSERT INTO shows (tvdb_id, imdb_id, title, year, status, last_refreshed, poster_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tvdb_id) DO UPDATE SET
            title=excluded.title, year=excluded.year,
            status=excluded.status, last_refreshed=excluded.last_refreshed,
            poster_url=COALESCE(excluded.poster_url, poster_url)
    """, (tvdb_id, imdb_id, title, year, status, datetime.now().isoformat(), poster_url))
    db.commit()

    c.execute("SELECT id FROM shows WHERE tvdb_id=?", (tvdb_id,))
    show_id = c.fetchone()[0]

    episodes = get_episodes(tvdb_id)
    for ep in episodes:
        if not ep.get("seasonNumber") or ep.get("seasonNumber") == 0:
            continue  # skip specials for now
        c.execute("""
            INSERT INTO episodes (show_id, tvdb_episode_id, season, episode, title, air_date)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tvdb_episode_id) DO UPDATE SET
                title=excluded.title, air_date=excluded.air_date
        """, (
            show_id,
            ep.get("id"),
            ep.get("seasonNumber"),
            ep.get("number"),
            ep.get("name"),
            ep.get("aired")
        ))

    db.commit()
    db.close()
    print(f"Cached: {title} ({len(episodes)} episodes)")
    return show_id

def refresh_show(show_id):
    """Re-fetch and update episode cache for a show"""
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("SELECT tvdb_id FROM shows WHERE id=?", (show_id,))
    row = c.fetchone()
    db.close()
    if row:
        cache_show(row[0])
