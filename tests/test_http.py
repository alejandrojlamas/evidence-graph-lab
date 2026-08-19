from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from red_privada.http import HTTPFetcher, InvalidUserAgent, RobotsDenied

VALID_USER_AGENT = "RedPrivadaBot/0.1 (+https://github.com/alejandrojlamas/Red-privada)"


def test_missing_robots_denies_fetch_by_default(tmp_path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404, request=request)

    fetcher = _fetcher(tmp_path, handler)
    try:
        with pytest.raises(RobotsDenied):
            fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert paths == ["/robots.txt"]


@pytest.mark.parametrize("robots_body", ["", "# no usable directives", "<html>error</html>"])
def test_empty_or_unusable_robots_denies_by_default(tmp_path, robots_body: str) -> None:
    fetcher = _fetcher(
        tmp_path,
        lambda request: httpx.Response(200, text=robots_body, request=request),
    )
    try:
        assert not fetcher.can_fetch("https://news.test/article")
    finally:
        fetcher.close()


def test_robots_network_error_denies_by_default(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    fetcher = _fetcher(tmp_path, handler)
    try:
        assert not fetcher.can_fetch("https://news.test/article")
    finally:
        fetcher.close()


def test_explicit_override_allows_only_unavailable_robots(tmp_path) -> None:
    fetcher = _fetcher(
        tmp_path,
        lambda request: httpx.Response(404, request=request),
        allow_robots_unavailable=True,
    )
    try:
        assert fetcher.can_fetch("https://news.test/article")
    finally:
        fetcher.close()


def test_override_does_not_bypass_explicit_disallow(tmp_path) -> None:
    robots = "User-agent: *\nDisallow: /private\n"
    fetcher = _fetcher(
        tmp_path,
        lambda request: httpx.Response(200, text=robots, request=request),
        allow_robots_unavailable=True,
    )
    try:
        assert not fetcher.can_fetch("https://news.test/private/report")
        assert fetcher.can_fetch("https://news.test/public/report")
    finally:
        fetcher.close()


@pytest.mark.parametrize("status_code", [401, 403])
def test_override_does_not_bypass_robots_access_denial(tmp_path, status_code: int) -> None:
    fetcher = _fetcher(
        tmp_path,
        lambda request: httpx.Response(status_code, request=request),
        allow_robots_unavailable=True,
    )
    try:
        assert not fetcher.can_fetch("https://news.test/article")
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "user_agent",
    [
        "",
        "RedPrivadaBot/0.1",
        "RedPrivadaBot/0.1 (contact: configure@example.org)",
        "RedPrivadaBot/0.1 (+https://example.org/contact)",
    ],
)
def test_network_collection_requires_real_contact_url(tmp_path, user_agent: str) -> None:
    with pytest.raises(InvalidUserAgent):
        HTTPFetcher(tmp_path, user_agent, request_delay_seconds=0)


def _fetcher(
    tmp_path,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    allow_robots_unavailable: bool = False,
) -> HTTPFetcher:
    fetcher = HTTPFetcher(
        tmp_path,
        VALID_USER_AGENT,
        request_delay_seconds=0,
        allow_robots_unavailable=allow_robots_unavailable,
    )
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    return fetcher
