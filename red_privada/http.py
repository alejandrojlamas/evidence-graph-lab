from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from red_privada.text import stable_hash

LOGGER = logging.getLogger(__name__)


class RobotsDenied(RuntimeError):
    pass


class InvalidUserAgent(ValueError):
    pass


class HTTPFetcher:
    def __init__(
        self,
        cache_dir: str | Path,
        user_agent: str,
        request_delay_seconds: float = 1.0,
        allow_robots_unavailable: bool = False,
    ):
        self._validate_user_agent(user_agent)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.request_delay_seconds = request_delay_seconds
        self.allow_robots_unavailable = allow_robots_unavailable
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request_at = 0.0
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": user_agent, "Accept-Language": "es-MX,es;q=0.9"},
        )

    def close(self) -> None:
        self.client.close()

    def cache_path(self, url: str) -> Path:
        parsed = urlparse(url)
        host = parsed.netloc.replace(":", "_")
        return self.cache_dir / host / f"{stable_hash(url, length=32)}.html"

    def fetch(self, url: str, use_cache: bool = True) -> tuple[str, Path, bool]:
        path = self.cache_path(url)
        if use_cache and path.exists():
            return path.read_text(encoding="utf-8", errors="replace"), path, True
        if not self.can_fetch(url):
            raise RobotsDenied(f"robots.txt policy blocks fetching {url}")
        self._rate_limit()
        LOGGER.info("fetching url=%s", url)
        response = self.client.get(url)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.text, encoding="utf-8")
        return response.text, path, False

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            self._robots[root] = self._load_robots(root)
        return self._robots[root].can_fetch(self.user_agent, url)

    def _load_robots(self, root: str) -> RobotFileParser:
        robot_url = f"{root}/robots.txt"
        try:
            self._rate_limit()
            response = self.client.get(robot_url)
        except httpx.HTTPError as exc:
            LOGGER.warning("robots.txt unavailable url=%s error=%s", robot_url, exc)
            return self._unavailable_robots_parser(robot_url)

        if response.status_code in {401, 403}:
            LOGGER.warning("robots.txt access denied url=%s status=%s", robot_url, response.status_code)
            return self._deny_all_parser(robot_url)
        if response.status_code >= 400:
            LOGGER.warning("robots.txt unavailable url=%s status=%s", robot_url, response.status_code)
            return self._unavailable_robots_parser(robot_url)

        lines = response.text.splitlines()
        has_user_agent = any(
            re.match(r"^\s*user-agent\s*:", line, flags=re.IGNORECASE)
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
        if not has_user_agent:
            LOGGER.warning("robots.txt missing usable rules url=%s", robot_url)
            return self._unavailable_robots_parser(robot_url)

        parser = RobotFileParser()
        parser.set_url(robot_url)
        parser.parse(lines)
        return parser

    def _unavailable_robots_parser(self, robot_url: str) -> RobotFileParser:
        if self.allow_robots_unavailable:
            LOGGER.warning(
                "robots.txt unavailable; allowing fetch because explicit override is enabled url=%s",
                robot_url,
            )
            return self._allow_all_parser(robot_url)
        return self._deny_all_parser(robot_url)

    @staticmethod
    def _allow_all_parser(robot_url: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.set_url(robot_url)
        parser.parse(["User-agent: *", "Allow: /"])
        return parser

    @staticmethod
    def _deny_all_parser(robot_url: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.set_url(robot_url)
        parser.parse(["User-agent: *", "Disallow: /"])
        return parser

    @staticmethod
    def _validate_user_agent(user_agent: str) -> None:
        normalized = " ".join(user_agent.split())
        placeholder_markers = ("example.com", "example.org", "configure", "change-me", "<", ">")
        has_contact_url = re.search(r"https?://[^\s)]+", normalized, flags=re.IGNORECASE)
        if (
            len(normalized) < 12
            or not has_contact_url
            or any(marker in normalized.lower() for marker in placeholder_markers)
        ):
            raise InvalidUserAgent(
                "A descriptive RED_PRIVADA_USER_AGENT with a real contact URL is required "
                "before network collection"
            )

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_delay_seconds:
            time.sleep(self.request_delay_seconds - elapsed)
        self._last_request_at = time.monotonic()
