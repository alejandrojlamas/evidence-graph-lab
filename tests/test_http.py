from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from red_privada.http import (
    HTTPFetcher,
    InvalidUserAgent,
    ResponseTooLarge,
    RobotsDenied,
    UnsafeURL,
)

VALID_USER_AGENT = "RedPrivada/0.1 (+https://github.com/alejandrojlamas/red-privada)"


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


def test_fetch_preserves_body_and_cache_contract(tmp_path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(_logical_request_url(request))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        return httpx.Response(200, text="contenido público", request=request)

    fetcher = _fetcher(tmp_path, handler)
    try:
        text, path, cached = fetcher.fetch("https://news.test/article")
        cached_text, cached_path, cached_again = fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert text == cached_text == "contenido público"
    assert path == cached_path
    assert not cached
    assert cached_again
    assert requests == ["https://news.test/robots.txt", "https://news.test/article"]


def test_redirect_revalidates_destination_robots(tmp_path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(_logical_request_url(request))
        if request.headers["host"] == "news.test" and request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        if request.headers["host"] == "news.test":
            return httpx.Response(
                302,
                headers={"Location": "https://archive.test/private/report"},
                request=request,
            )
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n", request=request)
        raise AssertionError("El destino bloqueado no debe solicitarse")

    fetcher = _fetcher(tmp_path, handler)
    try:
        with pytest.raises(RobotsDenied):
            fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert requests == [
        "https://news.test/robots.txt",
        "https://news.test/article",
        "https://archive.test/robots.txt",
    ]


def test_redirect_to_allowed_https_destination_preserves_response(tmp_path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(_logical_request_url(request))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        if request.headers["host"] == "news.test":
            return httpx.Response(
                302,
                headers={"Location": "https://archive.test/public/report"},
                request=request,
            )
        return httpx.Response(200, text="respuesta final", request=request)

    fetcher = _fetcher(tmp_path, handler)
    try:
        text, path, cached = fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert text == "respuesta final"
    assert path.read_text(encoding="utf-8") == text
    assert not cached
    assert requests == [
        "https://news.test/robots.txt",
        "https://news.test/article",
        "https://archive.test/robots.txt",
        "https://archive.test/public/report",
    ]


def test_https_redirect_cannot_downgrade_to_http(tmp_path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(_logical_request_url(request))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        return httpx.Response(
            302,
            headers={"Location": "http://news.test/insecure"},
            request=request,
        )

    fetcher = _fetcher(tmp_path, handler)
    try:
        with pytest.raises(UnsafeURL, match="degradar una conexión HTTPS"):
            fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert requests == ["https://news.test/robots.txt", "https://news.test/article"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://news.test/article"])
def test_non_http_schemes_are_rejected_before_request(tmp_path, url: str) -> None:
    fetcher = _fetcher(
        tmp_path,
        lambda request: pytest.fail(f"No debía solicitarse {request.url}"),
    )
    try:
        with pytest.raises(UnsafeURL, match="HTTP o HTTPS"):
            fetcher.fetch(url)
    finally:
        fetcher.close()


@pytest.mark.parametrize("url", ["http://../private", "http://%31%32%37.0.0.1/private"])
def test_malformed_hosts_are_rejected_before_cache_or_request(tmp_path, url: str) -> None:
    fetcher = _fetcher(
        tmp_path,
        lambda request: pytest.fail(f"No debía solicitarse {request.url}"),
    )
    try:
        with pytest.raises(UnsafeURL, match="host válido"):
            fetcher.fetch(url)
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/private",
        "http://127.0.0.1/private",
        "http://127.1/private",
        "http://2130706433/private",
        "http://0x7f000001/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/private",
    ],
)
def test_local_and_non_public_literal_hosts_are_rejected(tmp_path, url: str) -> None:
    fetcher = _fetcher(
        tmp_path,
        lambda request: pytest.fail(f"No debía solicitarse {request.url}"),
    )
    try:
        with pytest.raises(UnsafeURL):
            fetcher.fetch(url)
    finally:
        fetcher.close()


def test_hostname_resolving_to_non_public_address_is_rejected(tmp_path) -> None:
    fetcher = HTTPFetcher(
        tmp_path,
        VALID_USER_AGENT,
        request_delay_seconds=0,
    )
    fetcher._host_resolver = lambda _host, _port: ["93.184.216.34", "127.0.0.1"]
    fetcher.client.close()
    fetcher.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"No debía solicitarse {request.url}")
        )
    )
    try:
        with pytest.raises(RobotsDenied):
            fetcher.fetch("https://news.example/article")
    finally:
        fetcher.close()


def test_default_client_disables_environment_and_tls_connection_reuse(
    tmp_path, monkeypatch
) -> None:
    client_options: dict[str, object] = {}
    real_client = httpx.Client

    def build_client(**kwargs) -> httpx.Client:
        client_options.update(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", build_client)
    fetcher = HTTPFetcher(
        tmp_path,
        VALID_USER_AGENT,
        request_delay_seconds=0,
    )
    try:
        assert client_options["trust_env"] is False
        limits = client_options["limits"]
        assert isinstance(limits, httpx.Limits)
        assert limits.max_keepalive_connections == 0
    finally:
        fetcher.close()


def test_dns_rebinding_cannot_replace_validated_connection_address(tmp_path) -> None:
    resolution_calls: list[tuple[str, int]] = []
    transport_requests: list[tuple[str, str, str | None]] = []

    def resolver(hostname: str, port: int) -> list[str]:
        resolution_calls.append((hostname, port))
        if len(resolution_calls) == 1:
            return ["93.184.216.34"]
        return ["127.0.0.1"]

    def handler(request: httpx.Request) -> httpx.Response:
        transport_requests.append(
            (
                request.url.host,
                request.headers["host"],
                request.extensions.get("sni_hostname"),
            )
        )
        return httpx.Response(200, text="contenido", request=request)

    fetcher = _fetcher(tmp_path, handler)
    fetcher._host_resolver = resolver
    fetcher._robots["https://news.test"] = fetcher._allow_all_parser("https://news.test/robots.txt")
    try:
        text, _, _ = fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert text == "contenido"
    assert resolution_calls == [("news.test", 443)]
    assert transport_requests == [("93.184.216.34", "news.test", "news.test")]


def test_validated_addresses_are_attempted_without_resolving_again(tmp_path) -> None:
    resolution_calls: list[tuple[str, int]] = []
    attempted_addresses: list[str] = []

    def resolver(hostname: str, port: int) -> list[str]:
        resolution_calls.append((hostname, port))
        return ["93.184.216.34", "93.184.216.35"]

    def handler(request: httpx.Request) -> httpx.Response:
        attempted_addresses.append(request.url.host)
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectError("primera dirección no disponible", request=request)
        assert request.headers["host"] == "news.test"
        assert request.extensions["sni_hostname"] == "news.test"
        return httpx.Response(200, text="respuesta alternativa", request=request)

    fetcher = _fetcher(tmp_path, handler)
    fetcher._host_resolver = resolver
    fetcher._robots["https://news.test"] = fetcher._allow_all_parser("https://news.test/robots.txt")
    try:
        text, _, _ = fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert text == "respuesta alternativa"
    assert resolution_calls == [("news.test", 443)]
    assert attempted_addresses == ["93.184.216.34", "93.184.216.35"]


def test_redirect_pins_each_host_and_preserves_host_and_sni(tmp_path) -> None:
    addresses = {
        "news.test": "93.184.216.34",
        "archive.test": "93.184.216.35",
    }
    resolution_calls: list[tuple[str, int]] = []
    transport_requests: list[tuple[str, str, str | None, str]] = []

    def resolver(hostname: str, port: int) -> list[str]:
        resolution_calls.append((hostname, port))
        return [addresses[hostname]]

    def handler(request: httpx.Request) -> httpx.Response:
        transport_requests.append(
            (
                request.url.host,
                request.headers["host"],
                request.extensions.get("sni_hostname"),
                request.url.raw_path.decode("ascii"),
            )
        )
        if request.headers["host"] == "news.test":
            return httpx.Response(
                302,
                headers={"Location": "https://archive.test/final?via=redirect"},
                request=request,
            )
        return httpx.Response(200, text="destino final", request=request)

    fetcher = _fetcher(tmp_path, handler)
    fetcher._host_resolver = resolver
    for root in ("https://news.test", "https://archive.test"):
        fetcher._robots[root] = fetcher._allow_all_parser(f"{root}/robots.txt")
    try:
        text, _, _ = fetcher.fetch("https://news.test/origen")
    finally:
        fetcher.close()

    assert text == "destino final"
    assert resolution_calls == [("news.test", 443), ("archive.test", 443)]
    assert transport_requests == [
        ("93.184.216.34", "news.test", "news.test", "/origen"),
        (
            "93.184.216.35",
            "archive.test",
            "archive.test",
            "/final?via=redirect",
        ),
    ]


def test_host_is_resolved_again_before_content_request(tmp_path) -> None:
    resolutions = iter([["93.184.216.34"], ["127.0.0.1"]])
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(_logical_request_url(request))
        return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)

    fetcher = HTTPFetcher(
        tmp_path,
        VALID_USER_AGENT,
        request_delay_seconds=0,
    )
    fetcher._host_resolver = lambda _host, _port: next(resolutions)
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UnsafeURL, match="resolvió a una dirección IP no pública"):
            fetcher.fetch("https://news.example/article")
    finally:
        fetcher.close()

    assert requests == ["https://news.example/robots.txt"]


def test_redirect_limit_is_enforced(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(HTTPFetcher, "MAX_REDIRECTS", 2)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(_logical_request_url(request))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        redirect_number = (
            int(request.url.path.rsplit("/", 1)[-1]) if "/redirect/" in request.url.path else 0
        )
        return httpx.Response(
            302,
            headers={"Location": f"/redirect/{redirect_number + 1}"},
            request=request,
        )

    fetcher = _fetcher(tmp_path, handler)
    try:
        with pytest.raises(UnsafeURL, match="límite de 2 redirecciones"):
            fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert requests == [
        "https://news.test/robots.txt",
        "https://news.test/article",
        "https://news.test/redirect/1",
        "https://news.test/redirect/2",
    ]


def test_response_limit_is_enforced_before_cache_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(HTTPFetcher, "MAX_RESPONSE_BYTES", 8)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        return httpx.Response(200, content=b"123456789", request=request)

    fetcher = _fetcher(tmp_path, handler)
    cache_path = fetcher.cache_path("https://news.test/article")
    try:
        with pytest.raises(ResponseTooLarge, match="límite es 8"):
            fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert not cache_path.exists()


def test_streamed_response_limit_is_enforced_without_content_length(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(HTTPFetcher, "MAX_RESPONSE_BYTES", 8)

    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"1234"
            yield b"56789"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        return httpx.Response(200, stream=OversizedStream(), request=request)

    fetcher = _fetcher(tmp_path, handler)
    cache_path = fetcher.cache_path("https://news.test/article")
    try:
        with pytest.raises(ResponseTooLarge, match="supera el límite de 8 bytes"):
            fetcher.fetch("https://news.test/article")
    finally:
        fetcher.close()

    assert not cache_path.exists()


def test_oversized_robots_fails_closed_even_with_unavailable_override(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(HTTPFetcher, "MAX_ROBOTS_BYTES", 16)
    fetcher = _fetcher(
        tmp_path,
        lambda request: httpx.Response(
            200,
            text="User-agent: *\nAllow: /\n",
            request=request,
        ),
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
        "EvidenceGraphLab/0.1",
        "EvidenceGraphLab/0.1 (contact: configure@example.org)",
        "EvidenceGraphLab/0.1 (+https://example.org/contact)",
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
    fetcher._host_resolver = lambda _host, _port: ["93.184.216.34"]
    fetcher.client.close()
    fetcher.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    return fetcher


def _logical_request_url(request: httpx.Request) -> str:
    return f"{request.url.scheme}://{request.headers['host']}{request.url.raw_path.decode('ascii')}"
