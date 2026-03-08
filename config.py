# Flame-TV Configuration
# Edit these values to match your setup

TVDB_API_KEY = "bf45cdbc-c6de-4af6-8b3e-7925091fc22a"
TVDB_BASE_URL = "https://api4.thetvdb.com/v4"

PROWLARR_URL = "http://localhost:9696"
PROWLARR_API_KEY = "YOUR_PROWLARR_API_KEY"  # Settings > General in Prowlarr

QBIT_URL = "http://localhost:8080"
QBIT_USERNAME = ""   # blank if no auth set
QBIT_PASSWORD = ""   # blank if no auth set
QBIT_CATEGORY = "flame-tv"
QBIT_SAVE_PATH = r"D:\downloads\flame-tv"

MEDIA_ROOT = r"D:\Media\TV"
DOWNLOAD_WATCH_PATH = r"D:\downloads\flame-tv"

# Quality preference order (first match wins)
QUALITY_ORDER = ["2160p", "4k", "uhd", "1080p"]

# Minimum file size in MB (skip tiny/fake files)
MIN_FILE_SIZE_MB = 500

# How often to poll qBit for completed downloads (seconds)
POLL_INTERVAL = 180

DB_PATH = "flame-tv.db"