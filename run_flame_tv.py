from waitress import serve
from app import app, log
from init_db import init
from worker import start_worker
import logging

log = logging.getLogger("flame-tv")

if __name__ == "__main__":
    init()
    log.info("Flame TV starting up.")
    start_worker()
    serve(app, host="127.0.0.1", port=5000, threads=4)