"""
settings.py — runtime settings loader
Reads from the DB settings table first, falls back to config.py values.
This means config.py is still the default/install-time config,
but anything saved via the Settings UI takes precedence.
"""
import sqlite3
import config

_DB_PATH = config.DB_PATH

def _get(key, fallback):
    try:
        db = sqlite3.connect(_DB_PATH)
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        db.close()
        if row and row[0] not in (None, ""):
            return row[0]
    except Exception:
        pass
    return fallback

def PROWLARR_URL():
    return _get("prowlarr_url", config.PROWLARR_URL)

def PROWLARR_API_KEY():
    return _get("prowlarr_api_key", config.PROWLARR_API_KEY)

def QBIT_URL():
    return _get("qbit_url", config.QBIT_URL)

def QBIT_USERNAME():
    return _get("qbit_username", config.QBIT_USERNAME)

def QBIT_PASSWORD():
    return _get("qbit_password", config.QBIT_PASSWORD)

def QBIT_CATEGORY():
    return _get("qbit_category", config.QBIT_CATEGORY)

def QBIT_SAVE_PATH():
    return _get("qbit_save_path", config.QBIT_SAVE_PATH)

def MEDIA_ROOT():
    return _get("media_root", config.MEDIA_ROOT)

def DOWNLOAD_WATCH_PATH():
    return _get("download_path", config.DOWNLOAD_WATCH_PATH)

def MIN_FILE_SIZE_MB():
    try:
        return int(_get("min_file_size_mb", str(config.MIN_FILE_SIZE_MB)))
    except ValueError:
        return config.MIN_FILE_SIZE_MB

def QUALITY_ORDER():
    raw = _get("quality_order", ",".join(config.QUALITY_ORDER))
    return [q.strip() for q in raw.split(",") if q.strip()]

def POLL_INTERVAL():
    return config.POLL_INTERVAL
