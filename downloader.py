import requests
import sqlite3
import time
from datetime import datetime
from config import DB_PATH
import settings

# ── Prowlarr ──────────────────────────────────────────────────────────────────

def search_prowlarr(query):
    """Search all Prowlarr indexers for a query string"""
    resp = requests.get(
        f"{settings.PROWLARR_URL()}/api/v1/search",
        params={"query": query, "type": "search"},
        headers={"X-Api-Key": settings.PROWLARR_API_KEY()}
    )
    resp.raise_for_status()
    return resp.json()

def score_result(result):
    """Score a search result — higher = better. Returns int."""
    title = (result.get("title") or "").lower()
    size_mb = (result.get("size") or 0) / (1024 * 1024)

    if size_mb < settings.MIN_FILE_SIZE_MB():
        return -1  # too small, likely fake

    score = 0
    for i, quality in enumerate(settings.QUALITY_ORDER()):
        if quality in title:
            score += (len(settings.QUALITY_ORDER()) - i) * 100
            break

    if "remux" in title:
        score += 50
    if "hdr" in title or "hdr10" in title or "dv" in title:
        score += 20
    if "hevc" in title or "x265" in title or "h265" in title:
        score += 10

    # Penalise known junk
    if "cam" in title or "ts." in title or "hdcam" in title:
        score -= 500

    return score

def pick_best(results):
    """Pick the best result from a list of Prowlarr results"""
    scored = [(score_result(r), r) for r in results]
    scored = [(s, r) for s, r in scored if s >= 0]
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def build_search_query(show_title, season, episode):
    """Build a search string e.g. Chicago Fire S13E09"""
    return f"{show_title} S{season:02d}E{episode:02d}"

# ── qBittorrent ───────────────────────────────────────────────────────────────

_qbit_session = None

def qbit_login():
    global _qbit_session
    s = requests.Session()
    username = settings.QBIT_USERNAME()
    if username:
        s.post(f"{settings.QBIT_URL()}/api/v2/auth/login", data={
            "username": username,
            "password": settings.QBIT_PASSWORD()
        })
    _qbit_session = s
    return s

def qbit():
    """Return an authenticated qBit session, re-logging in if the session has expired."""
    global _qbit_session
    if not _qbit_session:
        return qbit_login()
    # Check session is still valid — qBit returns "Forbidden" plain text if expired
    try:
        r = _qbit_session.get(f"{settings.QBIT_URL()}/api/v2/app/version", timeout=5)
        if r.status_code == 403 or r.text.strip().lower() == "forbidden":
            return qbit_login()
    except Exception:
        return qbit_login()
    return _qbit_session

def add_torrent(magnet_or_url):
    """Push a magnet link or torrent URL to qBit under the configured category."""
    data = {
        "urls": magnet_or_url,
        "category": settings.QBIT_CATEGORY()
    }
    resp = qbit().post(
        f"{settings.QBIT_URL()}/api/v2/torrents/add",
        data=data,
        timeout=15
    )
    resp.raise_for_status()
    return resp.text.strip() == "Ok."

def get_completed_torrents():
    """Return all completed torrents in the flame-tv category"""
    resp = qbit().get(f"{settings.QBIT_URL()}/api/v2/torrents/info",
                      params={"category": settings.QBIT_CATEGORY(), "filter": "completed"})
    resp.raise_for_status()
    return resp.json()

def remove_torrent(torrent_hash, delete_files=False):
    qbit().post(f"{settings.QBIT_URL()}/api/v2/torrents/delete", data={
        "hashes": torrent_hash,
        "deleteFiles": str(delete_files).lower()
    })

def get_torrent_hash_by_name(name, retries=15, delay=1.5):
    """Try to find a torrent hash in qBit by fuzzy name match, retrying briefly after add."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return None

    wanted_tokens = set(wanted.replace(".", " ").replace("-", " ").split())

    for _ in range(retries):
        try:
            resp = qbit().get(
                f"{settings.QBIT_URL()}/api/v2/torrents/info",
                params={"category": settings.QBIT_CATEGORY()},
                timeout=10
            )
            resp.raise_for_status()

            torrents = resp.json()

            # direct contains match
            for t in torrents:
                qname = (t.get("name") or "").strip().lower()
                if qname and (qname in wanted or wanted in qname):
                    return t.get("hash")

            # token overlap fallback
            best_hash = None
            best_score = 0

            for t in torrents:
                qname = (t.get("name") or "").strip().lower()
                if not qname:
                    continue

                q_tokens = set(qname.replace(".", " ").replace("-", " ").split())
                score = len(wanted_tokens & q_tokens)

                if score > best_score:
                    best_score = score
                    best_hash = t.get("hash")

            if best_score >= 4:
                return best_hash

        except Exception:
            pass

        time.sleep(delay)

    return None

# ── Search + queue ─────────────────────────────────────────────────────────────

def search_and_queue(show_id, season, episode, episode_id):
    """Search Prowlarr for an episode and push best result to qBit"""
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("SELECT title FROM shows WHERE id=?", (show_id,))
    row = c.fetchone()
    db.close()

    if not row:
        print(f"Show {show_id} not found")
        return False

    query = build_search_query(row[0], season, episode)
    print(f"Searching: {query}")

    try:
        results = search_prowlarr(query)
    except Exception as e:
        print(f"Prowlarr search failed: {e}")
        return False

    best = pick_best(results)
    if not best:
        print(f"No usable results for {query}")
        return False

    magnet = best.get("downloadUrl") or best.get("magnetUrl")
    if not magnet:
        print("No download URL in result")
        return False

    release_title = best.get("title", "")
    print(f"Pushing to qBit: {release_title}")

    try:
        ok = add_torrent(magnet)
    except Exception as e:
        print(f"qBit add failed: {e}")
        return False

    if not ok:
        print("qBit did not confirm add")
        return False

    torrent_hash = get_torrent_hash_by_name(release_title, retries=15, delay=1.5)

    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("UPDATE episodes SET status='downloading' WHERE id=?", (episode_id,))
    c.execute("""
        INSERT OR IGNORE INTO downloads (episode_id, torrent_hash, torrent_name, state, added_at)
        VALUES (?, ?, ?, 'downloading', ?)
    """, (episode_id, torrent_hash, release_title, datetime.now().isoformat()))
    db.commit()
    db.close()

    if torrent_hash:
        print(f"Queued OK (hash: {torrent_hash})")
    else:
        print("Queued to qBit, but hash could not be resolved yet")

    return True