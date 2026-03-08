import os
import re
import shutil
import sqlite3
import logging
from config import MEDIA_ROOT, DB_PATH

log = logging.getLogger("flame-tv.organiser")
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}
SXXEXX_RE = re.compile(r's(\d{1,2})e(\d{1,2})', re.IGNORECASE)

def clean_title(title):
    """Make a title safe for use in a filename"""
    title = re.sub(r'[<>:"/\\|?*]', '', title)
    title = title.strip('. ')
    return title

def find_all_video_files(folder):
    """Return all video files in a folder, sorted largest first."""
    found = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                full = os.path.join(root, f)
                found.append((os.path.getsize(full), full))
    found.sort(reverse=True)
    return [path for size, path in found]

def organise_pack(show_id, torrent_hash, source_path):
    """
    Handle a season pack — walk all video files, parse SxxExx from filenames,
    match to DB episodes, and organise each one.
    Returns count of episodes successfully organised.
    """
    if not os.path.isdir(source_path):
        log.warning(f"organise_pack: source is not a folder: {source_path}")
        return 0

    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    show = c.execute("SELECT title, media_path FROM shows WHERE id=?", (show_id,)).fetchone()
    db.close()

    if not show:
        return 0

    show_title, media_path = show
    organised = 0

    for video in find_all_video_files(source_path):
        fname = os.path.basename(video)
        m = SXXEXX_RE.search(fname)
        if not m:
            log.info(f"Pack: skipping (no SxxExx): {fname}")
            continue

        season = int(m.group(1))
        ep_num = int(m.group(2))

        db = sqlite3.connect(DB_PATH)
        c = db.cursor()
        ep = c.execute("""
            SELECT id, title, status FROM episodes
            WHERE show_id=? AND season=? AND episode=?
        """, (show_id, season, ep_num)).fetchone()
        db.close()

        if not ep:
            log.info(f"Pack: no DB match for S{season:02d}E{ep_num:02d} — skipping")
            continue

        ep_id, ep_title, ep_status = ep
        if ep_status == "have_it":
            log.info(f"Pack: already have S{season:02d}E{ep_num:02d} — skipping")
            continue

        show_dir = media_path or os.path.join(MEDIA_ROOT, clean_title(show_title))
        season_dir = os.path.join(show_dir, f"Season {season}")
        os.makedirs(season_dir, exist_ok=True)

        ext = os.path.splitext(video)[1].lower()
        safe_title = clean_title(ep_title or f"Episode {ep_num}")
        dest = os.path.join(season_dir, f"{ep_num:02d} - {safe_title}{ext}")

        if os.path.exists(dest):
            log.info(f"Pack: already exists: {dest}")
        else:
            log.info(f"Pack move: {fname} → {dest}")
            shutil.move(video, dest)

        db = sqlite3.connect(DB_PATH)
        c = db.cursor()
        c.execute("UPDATE episodes SET status='have_it', file_path=? WHERE id=?", (dest, ep_id))
        c.execute("""
            UPDATE downloads SET state='completed', completed_at=datetime('now')
            WHERE torrent_hash=? AND episode_id=?
        """, (torrent_hash, ep_id))
        db.commit()
        db.close()
        organised += 1

    log.info(f"Pack organised {organised} episode(s) from {os.path.basename(source_path)}")
    return organised


def organise_episode(episode_id, source_path):
    """
    Rename and move a completed download to the correct Jellyfin-friendly location.
    source_path = folder or file from qBit
    """
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    c.execute("""
        SELECT e.season, e.episode, e.title, s.title, s.media_path
        FROM episodes e
        JOIN shows s ON e.show_id = s.id
        WHERE e.id = ?
    """, (episode_id,))
    row = c.fetchone()

    if not row:
        print(f"Episode {episode_id} not found in DB")
        db.close()
        return False

    season, ep_num, ep_title, show_title, media_path = row

    # Work out destination
    show_dir = media_path or os.path.join(MEDIA_ROOT, clean_title(show_title))
    season_dir = os.path.join(show_dir, f"Season {season}")
    os.makedirs(season_dir, exist_ok=True)

    # Find the actual video file
    if os.path.isdir(source_path):
        video = find_video_file(source_path)
    else:
        video = source_path if os.path.splitext(source_path)[1].lower() in VIDEO_EXTENSIONS else None

    if not video:
        print(f"No video file found in {source_path}")
        db.close()
        return False

    ext = os.path.splitext(video)[1].lower()
    safe_ep_title = clean_title(ep_title or f"Episode {ep_num}")
    dest_filename = f"{ep_num:02d} - {safe_ep_title}{ext}"
    dest = os.path.join(season_dir, dest_filename)

    # Don't overwrite if already there
    if os.path.exists(dest):
        print(f"Already exists: {dest}")
    else:
        print(f"Moving: {video} → {dest}")
        shutil.move(video, dest)

    # Update DB
    c.execute("UPDATE episodes SET status='have_it', file_path=? WHERE id=?", (dest, episode_id))
    db.commit()
    db.close()

    print(f"Organised: {dest_filename}")
    return dest

def check_crossover_slot(episode_id):
    """
    Check if this episode is part of a crossover arc.
    If so, also organise the related episodes into the main show's folder.
    Returns list of related episode IDs to also grab if not yet downloaded.
    """
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    c.execute("""
        SELECT e.season, e.episode, e.show_id
        FROM episodes e WHERE e.id = ?
    """, (episode_id,))
    row = c.fetchone()
    if not row:
        db.close()
        return []

    season, episode, show_id = row

    c.execute("""
        SELECT related_show_id, related_season, related_episode, play_order, arc_name
        FROM crossovers
        WHERE show_id=? AND season=? AND episode=?
        ORDER BY play_order
    """, (show_id, season, episode))

    crossovers = c.fetchall()
    db.close()

    if crossovers:
        print(f"Episode is part of crossover arc: {crossovers[0][4]}")

    return crossovers