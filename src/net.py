"""Shared polite HTTP session: retries with backoff, per-host spacing, sane timeouts."""
from __future__ import annotations

import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 job-radar/1.0 (personal job search; contact via GitHub)"
)
TIMEOUT = (6, 20)  # connect, read
MIN_HOST_INTERVAL = 0.35  # seconds between requests to the same host

_local = threading.local()
_host_lock = threading.Lock()
_host_last: dict[str, float] = {}


def session() -> requests.Session:
    """One session per thread (requests.Session is not thread-safe)."""
    s: requests.Session | None = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        retry = Retry(
            total=3, connect=2, read=2, status=3, backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/xml;q=0.9, */*;q=0.8"})
        _local.session = s
    return s


def _space(url: str) -> None:
    host = urlsplit(url).netloc
    with _host_lock:
        now = time.monotonic()
        wait = _host_last.get(host, 0) + MIN_HOST_INTERVAL - now
        _host_last[host] = max(now, _host_last.get(host, 0) + MIN_HOST_INTERVAL)
    if wait > 0:
        time.sleep(wait + random.uniform(0, 0.15))


def get(url: str, **kw: Any) -> requests.Response:
    _space(url)
    resp = session().get(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    return resp


def post(url: str, **kw: Any) -> requests.Response:
    _space(url)
    return session().post(url, timeout=kw.pop("timeout", TIMEOUT), **kw)


def get_json(url: str, **kw: Any) -> Any:
    resp = get(url, **kw)
    resp.raise_for_status()
    return resp.json()
