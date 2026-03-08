import sqlite3
from config import DB_PATH

def init():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS shows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tvdb_id INTEGER UNIQUE NOT NULL,
            imdb_id TEXT,
            title TEXT NOT NULL,
            year INTEGER,
            status TEXT,         -- continuing / ended
            monitored INTEGER DEFAULT 1,
            media_path TEXT,     -- e.g. D:\\Media\\TV\\Chicago Fire
            last_refreshed TEXT,
            poster_url TEXT
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            show_id INTEGER NOT NULL,
            tvdb_episode_id INTEGER UNIQUE,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            title TEXT,
            air_date TEXT,
            status TEXT DEFAULT 'wanted',  -- wanted / downloading / have_it / skipped
            file_path TEXT,
            FOREIGN KEY(show_id) REFERENCES shows(id)
        );

        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER,
            torrent_hash TEXT UNIQUE,
            torrent_name TEXT,
            state TEXT,          -- queued / downloading / completed / failed
            added_at TEXT,
            completed_at TEXT,
            FOREIGN KEY(episode_id) REFERENCES episodes(id)
        );

        CREATE TABLE IF NOT EXISTS crossovers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            show_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            related_show_id INTEGER NOT NULL,
            related_season INTEGER NOT NULL,
            related_episode INTEGER NOT NULL,
            play_order INTEGER NOT NULL,  -- 1=first, 2=second etc in the arc
            arc_name TEXT,
            FOREIGN KEY(show_id) REFERENCES shows(id),
            FOREIGN KEY(related_show_id) REFERENCES shows(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    # Migrate existing DBs — add poster_url if missing
    cols = [r[1] for r in c.execute("PRAGMA table_info(shows)").fetchall()]
    if "poster_url" not in cols:
        c.execute("ALTER TABLE shows ADD COLUMN poster_url TEXT")

    db.commit()
    db.close()
    print("Database initialised OK")

if __name__ == "__main__":
    init()
