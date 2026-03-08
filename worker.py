import sqlite3
import time
import threading
import logging
from datetime import datetime, date

from config import DB_PATH
from downloader import get_completed_torrents
from organiser import organise_episode, organise_pack
from downloader import search_and_queue

log = logging.getLogger("flame-tv.worker")

# Track when we last ran each task
_last_qbit_poll = 0
_last_db_clean = 0
_last_auto_grab = 0

QBIT_POLL_SECS = 900      # check downloads every 15 min
AUTO_GRAB_SECS = 1800     # auto-check released episodes every 30 min
DB_CLEAN_SECS = 3600      # clean stale records every hour


def poll_qbit():
    log.info("Polling qBittorrent for completed downloads...")
    try:
        completed = get_completed_torrents()
    except Exception as e:
        log.warning(f"qBit poll failed: {e}")
        return

    if not completed:
        log.info("qBit: nothing completed yet.")
        return

    for torrent in completed:
        hash_ = torrent.get("hash")
        name  = torrent.get("name")
        content_path = torrent.get("content_path") or torrent.get("save_path")

        db = sqlite3.connect(DB_PATH)
        c = db.cursor()
        # Find all episodes linked to this hash
        rows = c.execute(
            "SELECT id, episode_id, state FROM downloads WHERE torrent_hash=?", (hash_,)
        ).fetchall()
        db.close()

        if not rows:
            continue

        # Skip if all already completed
        if all(r[2] == "completed" for r in rows):
            continue

        log.info(f"Organising: {name} ({len(rows)} episode(s))")

        if len(rows) > 1:
            # Season pack — get show_id from first episode and use pack organiser
            db = sqlite3.connect(DB_PATH)
            c = db.cursor()
            show_id = c.execute(
                "SELECT show_id FROM episodes WHERE id=?", (rows[0][1],)
            ).fetchone()
            db.close()
            if show_id:
                organise_pack(show_id[0], hash_, content_path)
        else:
            # Single episode
            dl_id, episode_id, state = rows[0]
            result = organise_episode(episode_id, content_path)
            if result:
                db = sqlite3.connect(DB_PATH)
                c = db.cursor()
                c.execute(
                    "UPDATE downloads SET state='completed', completed_at=? WHERE id=?",
                    (datetime.now().isoformat(), dl_id),
                )
                db.commit()
                db.close()
                log.info(f"Organised OK: {name}")
            else:
                log.warning(f"Organise failed for: {name}")


def auto_grab_released():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    today = date.today().isoformat()

    episodes = c.execute("""
        SELECT e.id, e.show_id, e.season, e.episode
        FROM episodes e
        JOIN shows s ON s.id = e.show_id
        WHERE s.monitored = 1
          AND e.status = 'wanted'
          AND e.air_date IS NOT NULL
          AND e.air_date <= ?
        ORDER BY e.air_date, e.season, e.episode
    """, (today,)).fetchall()
    db.close()

    if not episodes:
        log.info("Auto-grab: nothing newly releasable.")
        return

    grabbed = 0
    for ep in episodes:
        ok = search_and_queue(ep[1], ep[2], ep[3], ep[0])
        if ok:
            grabbed += 1

    if grabbed:
        log.info(f"Auto-grab queued {grabbed} released episode(s)")
    else:
        log.info("Auto-grab found candidates, but nothing usable was queued.")


def clean_stale():
    """Mark any downloads stuck in 'downloading' state for >24h as failed."""
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("""
        UPDATE downloads SET state='failed'
        WHERE state='downloading'
          AND added_at < datetime('now', '-24 hours')
    """)
    changed = c.rowcount
    db.commit()
    db.close()

    if changed:
        log.warning(f"Marked {changed} stale download(s) as failed.")


def start_worker():
    """Periodic background worker — sleeps between runs, no busy loop."""
    def loop():
        global _last_qbit_poll, _last_db_clean, _last_auto_grab

        log.info(
            f"Worker started — qBit poll every {QBIT_POLL_SECS}s, "
            f"auto-grab every {AUTO_GRAB_SECS}s, "
            f"DB clean every {DB_CLEAN_SECS}s"
        )

        while True:
            now = time.time()

            if now - _last_qbit_poll >= QBIT_POLL_SECS:
                poll_qbit()
                _last_qbit_poll = time.time()

            if now - _last_auto_grab >= AUTO_GRAB_SECS:
                try:
                    auto_grab_released()
                except Exception as e:
                    log.exception(f"Auto grab failed: {e}")
                _last_auto_grab = time.time()

            if now - _last_db_clean >= DB_CLEAN_SECS:
                clean_stale()
                _last_db_clean = time.time()

            time.sleep(60)  # wake once a minute and check what's due

    t = threading.Thread(target=loop, daemon=True)
    t.start()