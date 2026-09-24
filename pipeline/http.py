import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

UA = "CoffeeSommelierBot/0.1 (+portfolio project)"


class RobotsDisallowed(Exception):
    pass


class PoliteClient:
    """HTTP GET with robots.txt checks and a per-host delay."""

    def __init__(self, delay: float = 1.0, user_agent: str = UA, transport=None, sleep=time.sleep, timeout: float = 30.0):
        self._client = httpx.Client(headers={"User-Agent": user_agent}, transport=transport,
                                    timeout=timeout, follow_redirects=True)
        self._delay = delay
        self._sleep = sleep
        self._ua = user_agent
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    def _robots_for(self, url: str):
        p = urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            # RFC 9309: 4xx means no rules (allow all); 5xx or unreachable means assume full disallow.
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                r = self._client.get(host + "/robots.txt")
                self._last[p.netloc] = time.monotonic()
                if r.status_code >= 500:
                    rp.disallow_all = True
                elif r.status_code >= 400:
                    rp = None
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                self._last[p.netloc] = time.monotonic()
                rp.disallow_all = True
            self._robots[host] = rp
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(self._ua, url)

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        if host in self._last:
            wait = self._delay - (time.monotonic() - self._last[host])
            if wait > 0:
                self._sleep(wait)
        r = self._client.get(url, params=params)
        self._last[host] = time.monotonic()
        r.raise_for_status()
        return r

    def download(self, url: str, dest: Path) -> Path:
        r = self.get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest
