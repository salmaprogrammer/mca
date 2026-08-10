"""Gunicorn configuration (sprint S10.2)."""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")

# Sync workers: this app does blocking DB work, makes no outbound API calls,
# and holds no long-lived connections, so threads/async buy nothing.
worker_class = "sync"
workers = int(os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 5)))
threads = 1

# Generous enough that a worker is never killed mid-request. The WhatsApp send
# button records the hand-off and then redirects; a kill in between would mark
# a message sent that nobody was ever handed a link for.
timeout = 45
graceful_timeout = 30
keepalive = 5

# Recycle workers to bound any slow leak; jitter avoids all workers restarting
# at the same moment.
max_requests = 1000
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
# Include the real client IP (set by nginx) so the login throttle and audit log
# record the visitor, not the proxy.
access_log_format = '%({X-Forwarded-For}i)s %(t)s "%(r)s" %(s)s %(b)s %(D)sµs'

preload_app = True
