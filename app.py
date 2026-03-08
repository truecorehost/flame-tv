from flask import Flask, render_template, request, redirect, jsonify, Response
import sqlite3
import threading
import logging
import os
import time as _time
import psutil
from config import DB_PATH
from metadata import search_show, cache_show
from downloader import search_and_queue
from worker import start_worker
from init_db import init
from scanner import scan_and_organise

app = Flask(__name__)

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flame-tv.log")

import re
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

class HumanLogHandler(logging.FileHandler):
    def emit(self, record):
        try:
            msg = _ANSI_RE.sub('', self.format(record))
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

class CleanStreamHandler(logging.StreamHandler):
    """StreamHandler that strips ANSI escape codes before printing."""
    def emit(self, record):
        record.msg = _ANSI_RE.sub('', str(record.msg))
        super().emit(record)

def setup_logging():
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                             datefmt="%d %b %Y %H:%M:%S")
    file_handler = HumanLogHandler(LOG_PATH)
    file_handler.setFormatter(fmt)
    stdout_handler = CleanStreamHandler()
    stdout_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stdout_handler)
    # Stop werkzeug injecting its own coloured lines into our log
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

setup_logging()
log = logging.getLogger("flame-tv")

# ── DB ────────────────────────────────────────────────────────────────────────

def db():
    return sqlite3.connect(DB_PATH)

# ── Settings ──────────────────────────────────────────────────────────────────

SETTINGS_FIELDS = [
    ("prowlarr_url",     "Prowlarr URL",          "text",     "http://localhost:9696"),
    ("prowlarr_api_key", "Prowlarr API Key",       "password", ""),
    ("qbit_url",         "qBittorrent URL",        "text",     "http://localhost:8080"),
    ("qbit_username",    "qBittorrent Username",   "text",     ""),
    ("qbit_password",    "qBittorrent Password",   "password", ""),
    ("qbit_category",    "qBit Category",          "text",     "flame-tv"),
    ("qbit_save_path",   "qBit Save Path",         "text",     r"D:\downloads\flame-tv"),
    ("media_root",       "Media Root (TV folder)", "text",     r"D:\Media\TV"),
    ("download_path",    "Download Watch Path",    "text",     r"D:\downloads\flame-tv"),
    ("min_file_size_mb", "Min File Size (MB)",     "number",   "500"),
    ("quality_order",    "Quality Order (CSV)",    "text",     "2160p,4k,uhd,1080p"),
]

def get_settings():
    c = db()
    rows = c.execute("SELECT key, value FROM settings").fetchall()
    c.close()
    vals = {r[0]: r[1] for r in rows}
    return {key: vals.get(key, default) for key, label, ftype, default in SETTINGS_FIELDS}

# ── Stats ─────────────────────────────────────────────────────────────────────

_start_time = _time.time()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    c = db()
    shows = c.execute("""
        SELECT s.id, s.title, s.year, s.status,
               COUNT(CASE WHEN e.status='have_it' THEN 1 END) as have_it,
               COUNT(CASE WHEN e.status='wanted' THEN 1 END) as wanted,
               COUNT(CASE WHEN e.status='downloading' THEN 1 END) as downloading,
               COUNT(e.id) as total,
               s.poster_url
        FROM shows s
        LEFT JOIN episodes e ON e.show_id = s.id
        WHERE s.monitored=1
        GROUP BY s.id
        ORDER BY s.title
    """).fetchall()
    c.close()
    return render_template("index.html", shows=shows)

@app.route("/search")
def search():
    query = request.args.get("q", "")
    results = []
    if query:
        results = search_show(query)
    return render_template("search.html", query=query, results=results)

@app.route("/add/<int:tvdb_id>", methods=["POST"])
def add_show(tvdb_id):
    show_id = cache_show(tvdb_id)
    return redirect(f"/show/{show_id}")

@app.route("/show/<int:show_id>")
def show_detail(show_id):
    c = db()
    show = c.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
    episodes = c.execute("""
        SELECT id, season, episode, title, air_date, status, file_path
        FROM episodes WHERE show_id=?
        ORDER BY season, episode
    """, (show_id,)).fetchall()
    c.close()
    seasons = {}
    for ep in episodes:
        s = ep[1]
        if s not in seasons:
            seasons[s] = []
        seasons[s].append(ep)
    return render_template("show.html", show=show, seasons=seasons)

@app.route("/grab/<int:episode_id>", methods=["POST"])
def grab_episode(episode_id):
    c = db()
    ep = c.execute("SELECT show_id, season, episode FROM episodes WHERE id=?", (episode_id,)).fetchone()
    c.close()
    if ep:
        ok = search_and_queue(ep[0], ep[1], ep[2], episode_id)
        return jsonify({"ok": ok})
    return jsonify({"ok": False, "error": "Episode not found"})

@app.route("/grab/season/<int:show_id>/<int:season>", methods=["POST"])
def grab_season(show_id, season):
    c = db()
    episodes = c.execute("""
        SELECT id, season, episode FROM episodes
        WHERE show_id=? AND season=? AND status='wanted'
    """, (show_id, season)).fetchall()
    c.close()
    queued = 0
    for ep in episodes:
        ok = search_and_queue(show_id, ep[1], ep[2], ep[0])
        if ok:
            queued += 1
    return jsonify({"queued": queued, "total": len(episodes)})

@app.route("/remove/<int:show_id>", methods=["POST"])
def remove_show(show_id):
    c = db()
    c.execute("UPDATE shows SET monitored=0 WHERE id=?", (show_id,))
    c.commit()
    c.close()
    return redirect("/")

@app.route("/refresh/posters")
def refresh_posters():
    from metadata import get_show_details
    c = db()
    shows = c.execute("SELECT id, tvdb_id, title FROM shows WHERE poster_url IS NULL OR poster_url=''").fetchall()
    c.close()
    updated = 0
    for show_id, tvdb_id, title in shows:
        try:
            details = get_show_details(tvdb_id)
            poster_url = None
            best_score = -1
            for art in details.get("artworks", []):
                if art.get("type") == 2:
                    score = art.get("score", 0) or 0
                    if score > best_score:
                        best_score = score
                        poster_url = art.get("image")
            if not poster_url:
                poster_url = details.get("image")
            if poster_url:
                c = db()
                c.execute("UPDATE shows SET poster_url=? WHERE id=?", (poster_url, show_id))
                c.commit()
                c.close()
                updated += 1
                log.info(f"Poster updated: {title}")
        except Exception as e:
            log.warning(f"Failed poster for {title}: {e}")
    return f"Done — updated {updated} of {len(shows)} shows. <a href='/'>Back to library</a>"

@app.route("/settings", methods=["GET", "POST"])
def settings():
    msg = None
    if request.method == "POST":
        c = db()
        for key, label, ftype, default in SETTINGS_FIELDS:
            val = request.form.get(key, "").strip()
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, val))
        c.commit()
        c.close()
        msg = "Settings saved."
        log.info("Settings updated via UI.")
    vals = get_settings()
    return render_template("settings.html", fields=SETTINGS_FIELDS, vals=vals, msg=msg)

@app.route("/stats")
def stats():
    proc = psutil.Process(os.getpid())
    mem_mb = proc.memory_info().rss / 1024 / 1024
    cpu = proc.cpu_percent(interval=0.2)
    uptime_secs = int(_time.time() - _start_time)
    hours, rem = divmod(uptime_secs, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{hours}h {mins}m {secs}s" if hours else f"{mins}m {secs}s"
    c = db()
    show_count   = c.execute("SELECT COUNT(*) FROM shows WHERE monitored=1").fetchone()[0]
    ep_total     = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    ep_have      = c.execute("SELECT COUNT(*) FROM episodes WHERE status='have_it'").fetchone()[0]
    ep_wanted    = c.execute("SELECT COUNT(*) FROM episodes WHERE status='wanted'").fetchone()[0]
    ep_dl        = c.execute("SELECT COUNT(*) FROM episodes WHERE status='downloading'").fetchone()[0]
    dl_total     = c.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
    dl_completed = c.execute("SELECT COUNT(*) FROM downloads WHERE state='completed'").fetchone()[0]
    c.close()
    import settings as s
    return render_template("stats.html",
        mem_mb=round(mem_mb, 1), cpu=cpu, uptime=uptime_str,
        show_count=show_count, ep_total=ep_total,
        ep_have=ep_have, ep_wanted=ep_wanted, ep_dl=ep_dl,
        dl_total=dl_total, dl_completed=dl_completed,
        media_root=s.MEDIA_ROOT(), prowlarr_url=s.PROWLARR_URL(), qbit_url=s.QBIT_URL(),
    )

@app.route("/logs")
def view_logs():
    lines = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            raw = f.readlines()
        for line in reversed(raw[-200:]):
            line = line.strip()
            if not line:
                continue
            if "  ERROR  " in line:
                css = "color:#f44"
            elif "  WARNING" in line:
                css = "color:#fa4"
            elif "  INFO   " in line:
                css = "color:#ccc"
            else:
                css = "color:#888"
            lines.append((css, line))
    return render_template("logs.html", lines=lines)

@app.route("/logs/clear", methods=["POST"])
def clear_logs():
    if os.path.exists(LOG_PATH):
        open(LOG_PATH, "w").close()
    return redirect("/logs")

# ── Scan streaming ────────────────────────────────────────────────────────────

_scan_running = False

def _stream_scan(dry_run=False):
    lines = []
    def cb(msg):
        lines.append(msg)
    done = threading.Event()
    def run():
        scan_and_organise(dry_run=dry_run, log_callback=cb)
        done.set()
    threading.Thread(target=run, daemon=True).start()
    sent = 0
    while not done.is_set() or sent < len(lines):
        while sent < len(lines):
            yield lines[sent]
            sent += 1
        _time.sleep(0.1)

@app.route("/scan/dry")
def scan_dry():
    def generate():
        yield "<html><head><title>Flame TV - Dry Run</title>"
        yield "<style>body{background:#111;color:#eee;font-family:monospace;padding:20px;} .move{color:#f90;} .ok{color:#4c4;} .skip{color:#888;} .err{color:#f44;}</style></head><body>"
        yield "<h2>🔍 Dry Run Scan — nothing will be moved</h2><pre>"
        for msg in _stream_scan(dry_run=True):
            if "WOULD MOVE" in msg or "→" in msg:
                yield f"<span class='move'>{msg}</span>\n"
            elif "OK:" in msg:
                yield f"<span class='ok'>{msg}</span>\n"
            elif "SKIP" in msg:
                yield f"<span class='skip'>{msg}</span>\n"
            elif "ERROR" in msg:
                yield f"<span class='err'>{msg}</span>\n"
            else:
                yield f"{msg}\n"
        yield "</pre><p><a href='/' style='color:#f60'>← Back to library</a></p></body></html>"
    return Response(generate(), mimetype="text/html")

@app.route("/scan/run", methods=["POST"])
def scan_run():
    def generate():
        global _scan_running
        if _scan_running:
            yield "Scan already running!\n"
            return
        _scan_running = True
        yield "<html><head><title>Flame TV - Scanning</title>"
        yield "<style>body{background:#111;color:#eee;font-family:monospace;padding:20px;} .move{color:#f90;} .ok{color:#4c4;} .skip{color:#888;} .err{color:#f44;}</style></head><body>"
        yield "<h2>🔧 Scanning & Organising...</h2><pre>"
        for msg in _stream_scan(dry_run=False):
            if "MOVE:" in msg or "→" in msg:
                yield f"<span class='move'>{msg}</span>\n"
            elif "OK:" in msg or "Done:" in msg:
                yield f"<span class='ok'>{msg}</span>\n"
            elif "SKIP" in msg:
                yield f"<span class='skip'>{msg}</span>\n"
            elif "ERROR" in msg:
                yield f"<span class='err'>{msg}</span>\n"
            else:
                yield f"{msg}\n"
        _scan_running = False
        yield "</pre><p><a href='/' style='color:#f60'>← Back to library</a></p></body></html>"
    return Response(generate(), mimetype="text/html")

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init()
    log.info("Flame TV starting up.")
    start_worker()
    app.run(host="127.0.0.1", port=5000, debug=False)