from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from red_privada.text import stable_hash

LOGGER = logging.getLogger(__name__)


class RobotsDenied(RuntimeError):
    pass


class InvalidUserAgent(ValueError):
    pass


class UnsafeURL(ValueError):
    pass


class ResponseTooLarge(RuntimeError):
    pass


class HTTPFetcher:
    MAX_REDIRECTS = 5
    MAX_ROBOTS_BYTES = 512 * 1024
    MAX_RESPONSE_BYTES = 10 * 1024 * 1024
    REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

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
        self._host_resolver = self._resolve_host
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request_at = 0.0
        self.client = httpx.Client(
            follow_redirects=False,
            timeout=30.0,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
            headers={"User-Agent": user_agent, "Accept-Language": "es-MX,es;q=0.9"},
        )

    def close(self) -> None:
        self.client.close()

    def cache_path(self, url: str) -> Path:
        self._validate_url(url)
        parsed = urlparse(url)
        host = parsed.netloc.replace(":", "_")
        return self.cache_dir / host / f"{stable_hash(url, length=32)}.html"

    def fetch(self, url: str, use_cache: bool = True) -> tuple[str, Path, bool]:
        self._validate_url(url)
        path = self.cache_path(url)
        if use_cache and path.exists():
            if path.stat().st_size > self.MAX_RESPONSE_BYTES:
                raise ResponseTooLarge(
                    f"La respuesta almacenada supera el límite de {self.MAX_RESPONSE_BYTES} bytes"
                )
            return path.read_text(encoding="utf-8", errors="replace"), path, True
        text, _, _ = self._get_with_redirects(
            url,
            max_bytes=self.MAX_RESPONSE_BYTES,
            check_robots=True,
            raise_for_status=True,
        )
        encoded = text.encode("utf-8")
        if len(encoded) > self.MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(
                f"La respuesta supera el límite de {self.MAX_RESPONSE_BYTES} bytes al guardarse"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        return text, path, False

    def can_fetch(self, url: str) -> bool:
        self._validate_url(url)
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            self._robots[root] = self._load_robots(root)
        return self._robots[root].can_fetch(self.user_agent, url)

    def _load_robots(self, root: str) -> RobotFileParser:
        robot_url = f"{root}/robots.txt"
        try:
            text, status_code, final_url = self._get_with_redirects(
                robot_url,
                max_bytes=self.MAX_ROBOTS_BYTES,
                check_robots=False,
                raise_for_status=False,
            )
        except (ResponseTooLarge, UnsafeURL) as exc:
            LOGGER.warning("robots.txt rechazado url=%s error=%s", robot_url, exc)
            return self._deny_all_parser(robot_url)
        except httpx.HTTPError as exc:
            LOGGER.warning("robots.txt no disponible url=%s error=%s", robot_url, exc)
            return self._unavailable_robots_parser(robot_url)

        if status_code in {401, 403}:
            LOGGER.warning("acceso a robots.txt denegado url=%s status=%s", final_url, status_code)
            return self._deny_all_parser(robot_url)
        if status_code >= 400:
            LOGGER.warning("robots.txt no disponible url=%s status=%s", final_url, status_code)
            return self._unavailable_robots_parser(robot_url)

        lines = text.splitlines()
        has_user_agent = any(
            re.match(r"^\s*user-agent\s*:", line, flags=re.IGNORECASE)
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
        if not has_user_agent:
            LOGGER.warning("robots.txt no contiene reglas utilizables url=%s", final_url)
            return self._unavailable_robots_parser(robot_url)

        parser = RobotFileParser()
        parser.set_url(final_url)
        parser.parse(lines)
        return parser

    def _get_with_redirects(
        self,
        url: str,
        *,
        max_bytes: int,
        check_robots: bool,
        raise_for_status: bool,
    ) -> tuple[str, int, str]:
        current_url = self._validate_url(url)
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            if check_robots and not self.can_fetch(current_url):
                raise RobotsDenied(f"La política robots.txt impide descargar {current_url}")

            resolved_addresses = self._validate_resolved_host(current_url)
            self._rate_limit()
            LOGGER.info("descargando url=%s", current_url)
            response = self._send_to_resolved_address(current_url, resolved_addresses)
            try:
                if response.status_code in self.REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeURL(f"Redirección sin encabezado Location desde {current_url}")
                    if redirect_count >= self.MAX_REDIRECTS:
                        raise UnsafeURL(
                            f"Se excedió el límite de {self.MAX_REDIRECTS} redirecciones"
                        )
                    next_url = urljoin(current_url, location)
                    current_url = self._validate_redirect(current_url, next_url)
                    continue

                if raise_for_status:
                    response.raise_for_status()
                if response.status_code >= 400:
                    return "", response.status_code, current_url
                body = self._read_limited(response, max_bytes)
                return self._decode_body(body, response.encoding), response.status_code, current_url
            finally:
                response.close()

        raise UnsafeURL(f"Se excedió el límite de {self.MAX_REDIRECTS} redirecciones")

    def _send_to_resolved_address(
        self,
        url: str,
        resolved_addresses: list[str],
    ) -> httpx.Response:
        logical_url = httpx.URL(url)
        original_host = logical_url.netloc.decode("ascii")
        sni_hostname = logical_url.raw_host.decode("ascii")
        last_error: httpx.TransportError | None = None

        for address in resolved_addresses:
            pinned_url = logical_url.copy_with(host=address)
            request = self.client.build_request(
                "GET",
                pinned_url,
                headers={"Host": original_host},
            )
            if logical_url.scheme == "https":
                request.extensions["sni_hostname"] = sni_hostname
            try:
                return self.client.send(request, stream=True, follow_redirects=False)
            except httpx.TransportError as exc:
                last_error = exc
                LOGGER.warning(
                    "falló la conexión a una dirección validada url=%s ip=%s error=%s",
                    url,
                    address,
                    exc,
                )

        if last_error is not None:
            raise last_error
        raise UnsafeURL(f"El host de {url} no tiene direcciones públicas validadas")

    @staticmethod
    def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                raise ResponseTooLarge(
                    f"La respuesta declara {declared_size} bytes; el límite es {max_bytes}"
                )

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLarge(f"La respuesta supera el límite de {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _decode_body(body: bytes, encoding: str | None) -> str:
        try:
            return body.decode(encoding or "utf-8", errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    @classmethod
    def _validate_redirect(cls, source_url: str, target_url: str) -> str:
        source = urlparse(source_url)
        target_url = cls._validate_url(target_url)
        target = urlparse(target_url)
        if source.scheme.lower() == "https" and target.scheme.lower() != "https":
            raise UnsafeURL(
                f"No se permite degradar una conexión HTTPS durante la redirección: {target_url}"
            )
        return target_url

    @staticmethod
    def _validate_url(url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise UnsafeURL("La URL debe ser una cadena no vacía")
        if "\r" in url or "\n" in url:
            raise UnsafeURL("La URL contiene caracteres de control")

        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise UnsafeURL(f"Solo se permiten URLs HTTP o HTTPS: {url}")
        if not parsed.hostname:
            raise UnsafeURL(f"La URL no contiene un host válido: {url}")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeURL("No se permiten credenciales embebidas en la URL")
        try:
            parsed.port
        except ValueError as exc:
            raise UnsafeURL(f"La URL contiene un puerto inválido: {url}") from exc

        hostname = parsed.hostname.rstrip(".").lower()
        if (
            not hostname
            or "%" in hostname
            or "\\" in hostname
            or any(char.isspace() for char in hostname)
        ):
            raise UnsafeURL(f"La URL no contiene un host válido: {url}")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise UnsafeURL(f"No se permite acceder a un host local: {hostname}")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            try:
                address = ipaddress.ip_address(socket.inet_aton(hostname))
            except OSError:
                address = None
        if address is not None and not address.is_global:
            raise UnsafeURL(f"No se permite acceder a una dirección IP no pública: {hostname}")
        return url

    def _validate_resolved_host(self, url: str) -> list[str]:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise UnsafeURL(f"La URL no contiene un host válido: {url}")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            resolved_addresses = list(self._host_resolver(hostname, port))
        except OSError as exc:
            raise UnsafeURL(f"No se pudo resolver el host {hostname}") from exc
        if not resolved_addresses:
            raise UnsafeURL(f"El host {hostname} no resolvió a ninguna dirección")
        validated_addresses: list[str] = []
        for resolved in resolved_addresses:
            try:
                address = ipaddress.ip_address(resolved.split("%", 1)[0])
            except ValueError as exc:
                raise UnsafeURL(
                    f"El host {hostname} resolvió a una dirección inválida: {resolved}"
                ) from exc
            if not address.is_global:
                raise UnsafeURL(
                    f"El host {hostname} resolvió a una dirección IP no pública: {resolved}"
                )
            normalized_address = str(address)
            if normalized_address not in validated_addresses:
                validated_addresses.append(normalized_address)
        return validated_addresses

    @staticmethod
    def _resolve_host(hostname: str, port: int) -> list[str]:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        }
        return sorted(addresses)

    def _unavailable_robots_parser(self, robot_url: str) -> RobotFileParser:
        if self.allow_robots_unavailable:
            LOGGER.warning(
                "robots.txt no disponible; descarga permitida por configuración explícita url=%s",
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
                "Se requiere RED_PRIVADA_USER_AGENT con una URL de contacto real antes de "
                "recolectar por red; EVIDENCE_GRAPH_USER_AGENT se conserva como alias 0.x"
            )

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_delay_seconds:
            time.sleep(self.request_delay_seconds - elapsed)
        self._last_request_at = time.monotonic()
