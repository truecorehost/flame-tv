🔥 Flame TV
A lightweight, self-hosted TV show manager. Automatically finds, downloads, and organises your TV library — without the bloat.
Built by TrueCore / Flame Software

Why Flame TV?
Most TV managers are massive. Sonarr runs at 300MB+ RAM, requires .NET, and comes with a sprawling config system most people never touch.
Flame TV does the same job in ~50MB, runs on plain Python, and gives you exactly what you need — nothing more.

🔍 Searches via Prowlarr (supports all your indexers)
⬇️ Downloads via qBittorrent
📁 Automatically renames and organises files into Show/Season X/ folders
📅 Tracks what you have, what's missing, and what's airing next
🖼️ Poster artwork from TVDB
📊 Live stats and human-readable logs
⚙️ All config via a web UI — no file editing required


Requirements

Python 3.9+
Prowlarr — for torrent indexing
qBittorrent with Web UI enabled
A TVDB API key (free)


Installation
1. Clone the repo
bashgit clone https://github.com/YOUR_USERNAME/flame-tv.git
cd flame-tv
2. Install dependencies
bashpip install -r requirements.txt
3. Configure
Open config.py and set your TVDB API key:
pythonTVDB_API_KEY = "your-key-here"
Everything else (Prowlarr URL, qBit credentials, media paths) is configured through the web UI after first run.
4. Run
bashpython run_flame_tv.py
Then open http://localhost:5000 in your browser.

For development/testing you can also use python app.py directly.


⚠️ qBittorrent MUST be configured BEFORE adding shows

Flame TV will happily queue **every missing episode** for every show you add.

On a show like **The Simpsons** (700–800 missing episodes possible), that means **hundreds of torrents queued at once** if qB is not throttled.

**Before you add your first show, set these in qBittorrent (Tools → Options):**

- **Max active downloads** — set to **5** or less  
- **Max active torrents** — set to **20–50** max  
- **Global download speed limit** — whatever your connection can actually handle  
- **Global upload speed limit** — be a good citizen, don't set to 0  

Also consider: BitTorrent → "Do not count slow torrents" so stalled ones don't block your queue.

**If you skip this and add The Simpsons anyway… don't say we didn't warn you. 🔥**


Go to Settings and fill in your Prowlarr URL + API key, qBittorrent details, and media root path
Use the Search bar to add your first show
Flame TV will fetch all episode data from TVDB automatically
Hit Get Missing on any show to start grabbing episodes


How It Works
Prowlarr (indexers) → Flame TV → qBittorrent → organised media folder
                          ↕
                       TVDB API
                      (metadata)

Every 30 minutes, Flame TV checks for newly aired episodes and queues them automatically
Every 15 minutes, it checks qBittorrent for completed downloads and organises them
Files are renamed to Show Name - SXXEXX - Episode Title.ext and moved to your media root


Directory Structure
C:\flame-tv\          (or wherever you clone it)
  app.py              Flask web app + all routes
  config.py           Default config (TVDB key lives here)
  settings.py         DB-driven settings loader
  metadata.py         TVDB API — show/episode data + posters
  downloader.py       Prowlarr search + qBit integration
  organiser.py        File renaming + moving
  scanner.py          Scan existing media folder
  worker.py           Background polling (downloads, auto-grab)
  run_flame_tv.py     Production runner (Waitress WSGI)
  init_db.py          Database setup + migrations
  requirements.txt
  templates/          Jinja2 HTML templates
  flame-tv.db         SQLite database (auto-created, not in git)
  flame-tv.log        Log file (auto-created, not in git)

Media Organisation
Flame TV expects (and creates) this folder structure:
D:\Media\TV\
  Show Name\
    Season 1\
      Show Name - S01E01 - Episode Title.mkv
    Season 2\
      ...
Compatible with Jellyfin, Plex, and Emby out of the box.

Alpha Notes
This is early software. It works well but expect rough edges. If something breaks, check Logs in the UI first — errors are logged in plain English.
Known limitations:

No multi-episode file support yet
Specials (Season 0) handling is basic
No duplicate detection (yet)


Contributing
PRs welcome. Keep it lean — the whole point is that this doesn't become Sonarr.

Licence
MIT — do what you like with it.

Built with 🔥 by TrueCore / Flame Software