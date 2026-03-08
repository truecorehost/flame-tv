import os
import re
import shutil
import sqlite3
import requests
from config import DB_PATH
import settings
from metadata import search_show, cache_show
from organiser import clean_title, VIDEO_EXTENSIONS

# ── Filename parser ────────────────────────────────────────────────────────────

JUNK_PREFIXES = re.compile(
    r'^(www\.\S+\s*-\s*|rvkd-|eztv\S*\s*-\s*)',
    re.IGNORECASE
)

SE_PATTERN = re.compile(
    r'[Ss](\d{1,2})[Ee](\d{1,2})',
)

# Matches season pack suffix e.g. .S01. and everything after
SEASON_PACK_PATTERN = re.compile(
    r'[\.\s][Ss]\d{1,2}[\.\s\[].*$'
)

# Matches release junk e.g. .2160p .BluRay .COMPLETE etc
RELEASE_JUNK_PATTERN = re.compile(
    r'[\.\s](19|20)\d{2}[\.\s].*$|[\.\s](2160p|1080p|720p|BluRay|WEB|HDTV|REMUX|COMPLETE|x264|x265|HEVC|HDR|DV).*$',
    re.IGNORECASE
)

def extract_show_name(raw_name):
    """
    Extract a clean show name from a messy folder name.
    e.g. 'House.Of.The.Dragon.S01.2160p.BluRay...' -> 'House of the Dragon'
    e.g. 'Chicago Fire' -> 'Chicago Fire'
    """
    name = raw_name
    name = SEASON_PACK_PATTERN.sub('', name)
    name = RELEASE_JUNK_PATTERN.sub('', name)
    # Replace dots with spaces only if no spaces present (dotted.name.style)
    if '.' in name and ' ' not in name:
        name = name.replace('.', ' ')
    return name.strip()

def parse_filename(filename):
    """
    Extract season + episode number from a messy filename.
    Returns (season, episode) as ints, or (None, None) if not found.
    """
    # Strip junk prefix first
    clean = JUNK_PREFIXES.sub('', filename)
    m = SE_PATTERN.search(clean)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None

def guess_show_name(path):
    """
    Guess show name from folder structure, cleaning release-style names.
    e.g. D:\\Media\\TV\\House.Of.The.Dragon.S01.2160p.BluRay...\\ep.mkv -> "House of the Dragon"
    """
    parts = path.replace('\\', '/').split('/')
    raw = None
    try:
        tv_idx = next(i for i, p in enumerate(parts) if p.lower() in ('tv', 'television', 'shows'))
        if tv_idx + 1 < len(parts):
            raw = parts[tv_idx + 1]
    except StopIteration:
        pass
    if not raw:
        raw = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return extract_show_name(raw)

# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_show_by_title(title):
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    # Exact match first
    c.execute("SELECT id, title FROM shows WHERE LOWER(title)=LOWER(?)", (title,))
    row = c.fetchone()
    if not row:
        # Fuzzy: only match if the search term is 5+ chars AND the DB title starts with it
        # This prevents "Psych" matching "PSYCHO-PASS" etc.
        if len(title) >= 5:
            c.execute("SELECT id, title FROM shows WHERE LOWER(title) LIKE LOWER(?)", (f'{title}%',))
            row = c.fetchone()
    db.close()
    return row

def get_episode(show_id, season, episode):
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("""
        SELECT id, title, status FROM episodes
        WHERE show_id=? AND season=? AND episode=?
    """, (show_id, season, episode))
    row = c.fetchone()
    db.close()
    return row

def mark_have_it(episode_id, file_path):
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("UPDATE episodes SET status='have_it', file_path=? WHERE id=?", (file_path, episode_id))
    db.commit()
    db.close()

# ── Auto-add show ──────────────────────────────────────────────────────────────

def auto_add_show(show_name):
    """Search TVDB for show_name and cache best match. Returns show DB id or None."""
    print(f"  Auto-adding: {show_name}")
    try:
        results = search_show(show_name)
    except Exception as e:
        print(f"  TVDB search failed: {e}")
        return None
    if not results:
        print(f"  Not found on TVDB: {show_name}")
        return None

    # Try to find an exact title match first, then fall back to first result
    best = None
    for r in results:
        if r['title'].lower() == show_name.lower():
            best = r
            break
    if not best:
        best = results[0]

    print(f"  Found: {best['title']} (TVDB {best['tvdb_id']})")
    try:
        show_id = cache_show(best['tvdb_id'])
        folder = os.path.join(settings.MEDIA_ROOT(), show_name)
        db = sqlite3.connect(DB_PATH)
        c = db.cursor()
        c.execute("UPDATE shows SET media_path=? WHERE id=?", (folder, show_id))
        db.commit()
        db.close()
        return show_id
    except Exception as e:
        print(f"  Failed to cache: {e}")
        return None

# ── Main scanner ───────────────────────────────────────────────────────────────

def scan_and_organise(dry_run=False, log_callback=None):
    """
    Walk settings.MEDIA_ROOT(), find all video files, parse them, rename and move to
    clean structure. If dry_run=True, just report what would happen.
    """
    def log(msg):
        print(msg)
        if log_callback:
            log_callback(msg)

    log(f"Scanning {settings.MEDIA_ROOT()}...")
    results = {"moved": 0, "already_good": 0, "skipped": 0, "failed": 0, "auto_added": 0}

    # Cache show lookups within this scan run to avoid hammering TVDB
    show_cache = {}  # raw_name -> (show_id, db_title) or None

    # Walk the whole tree
    for root, dirs, files in os.walk(settings.MEDIA_ROOT()):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue

            full_path = os.path.join(root, filename)
            season, episode = parse_filename(filename)

            if season is None:
                log(f"  SKIP (no S/E found): {filename}")
                results["skipped"] += 1
                continue

            # Guess show from folder
            show_name = guess_show_name(full_path)
            if not show_name:
                log(f"  SKIP (can't guess show): {filename}")
                results["skipped"] += 1
                continue

            # Find or auto-add show in DB (cached)
            if show_name in show_cache:
                show_row = show_cache[show_name]
            else:
                show_row = get_show_by_title(show_name)
                if not show_row:
                    show_id = auto_add_show(show_name)
                    if show_id:
                        results["auto_added"] += 1
                        c = sqlite3.connect(DB_PATH)
                        title = c.execute("SELECT title FROM shows WHERE id=?", (show_id,)).fetchone()[0]
                        c.close()
                        show_row = (show_id, title)
                    else:
                        show_row = None
                show_cache[show_name] = show_row

            if not show_row:
                log(f"  SKIP (show not found): {show_name}")
                results["skipped"] += 1
                continue

            show_id, db_show_title = show_row

            # Get episode from DB
            ep_row = get_episode(show_id, season, episode)
            if not ep_row:
                log(f"  SKIP (ep not in DB): {db_show_title} S{season:02d}E{episode:02d}")
                results["skipped"] += 1
                continue

            ep_id, ep_title, ep_status = ep_row
            safe_ep_title = clean_title(ep_title or f"Episode {episode}")

            # Build destination
            show_dir = os.path.join(settings.MEDIA_ROOT(), clean_title(db_show_title))
            season_dir = os.path.join(show_dir, f"Season {season}")
            dest_filename = f"{episode:02d} - {safe_ep_title}{ext}"
            dest = os.path.join(season_dir, dest_filename)

            if full_path == dest:
                log(f"  OK: {dest_filename}")
                mark_have_it(ep_id, dest)
                results["already_good"] += 1
                continue

            if dry_run:
                log(f"  WOULD MOVE: {filename}")
                log(f"         → {dest}")
                results["moved"] += 1
                continue

            # Do the move
            try:
                os.makedirs(season_dir, exist_ok=True)
                if os.path.exists(dest):
                    log(f"  DEST EXISTS, skipping: {dest_filename}")
                    results["skipped"] += 1
                    continue
                log(f"  MOVE: {filename}")
                log(f"     → {dest}")
                shutil.move(full_path, dest)
                mark_have_it(ep_id, dest)
                results["moved"] += 1
            except Exception as e:
                log(f"  ERROR moving {filename}: {e}")
                results["failed"] += 1

    # Clean up empty folders left behind
    if not dry_run:
        log("\nCleaning up empty folders...")
        removed_dirs = 0
        for root, dirs, files in os.walk(settings.MEDIA_ROOT(), topdown=False):
            if root == settings.MEDIA_ROOT():
                continue
            # Skip if it has any video files or subdirs with content
            has_content = any(
                os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
                for f in files
            )
            if not has_content and not os.listdir(root):
                try:
                    os.rmdir(root)
                    log(f"  Removed empty: {root}")
                    removed_dirs += 1
                except Exception:
                    pass
        log(f"  Removed {removed_dirs} empty folders")

    log(f"\nDone. Moved: {results['moved']} | Already good: {results['already_good']} | "
        f"Skipped: {results['skipped']} | Failed: {results['failed']} | "
        f"Auto-added shows: {results['auto_added']}")
    return results


if __name__ == "__main__":
    import sys
    dry = "--dry" in sys.argv
    if dry:
        print("DRY RUN — nothing will be moved\n")
    scan_and_organise(dry_run=dry)
