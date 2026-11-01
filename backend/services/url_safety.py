import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from config.settings import MAX_DOWNLOAD_BYTES, MAX_REDIRECTS

TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=15.0)
USER_AGENT = "VeritraceBot/1.0 (claim verification prototype)"
BLOCKED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".mov", ".avi",
    ".mp3", ".wav", ".zip", ".rar", ".gz", ".tar", ".exe", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx",
)
TRACKING_PARAMS = {"fbclid"}

Resolver = Callable[[str], Awaitable[list[str]]]


class UnsafeURL(Exception):
    pass


class FetchError(Exception):
    pass


@dataclass
class FetchedPage:
    url: str
    content_type: str  # "html" or "pdf"
    body: bytes
    encoding: str | None


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        urlencode(query),
        "",
    ))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_unsafe_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not ip.is_global or ip.is_multicast or ip.is_reserved


def precheck_url(url: str) -> str | None:
    """Return a rejection reason without using the network, or None."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "invalid_url"
    if parts.scheme not in ("http", "https") or not host:
        return "invalid_url"
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return "unsafe_destination"
    try:
        if is_unsafe_ip(host):
            return "unsafe_destination"
    except ValueError:
        pass
    if parts.path.lower().endswith(BLOCKED_EXTENSIONS):
        return "unsupported_file_type"
    return None


async def default_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class SafeFetcher:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = default_resolver,
    ):
        self.client = httpx.AsyncClient(
            transport=transport,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self.resolver = resolver

    async def close(self) -> None:
        await self.client.aclose()

    async def _check_destination(self, url: str) -> None:
        reason = precheck_url(url)
        if reason == "unsupported_file_type":
            raise FetchError(reason)
        if reason:
            raise UnsafeURL(reason)
        host = urlsplit(url).hostname
        try:
            addresses = await self.resolver(host)
        except OSError as exc:
            raise FetchError("dns_failure") from exc
        if not addresses or any(is_unsafe_ip(a) for a in addresses):
            raise UnsafeURL("unsafe_destination")

    async def fetch(self, url: str) -> FetchedPage:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await self._check_destination(current)
            try:
                async with self.client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("bad_redirect")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"http_{response.status_code}")
                    body = await self._read_limited(response)
                    return self._build_page(current, response, body)
            except httpx.HTTPError as exc:
                raise FetchError(type(exc).__name__) from exc
        raise FetchError("too_many_redirects")

    @staticmethod
    async def _read_limited(response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
            raise FetchError("too_large")
        chunks = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise FetchError("too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _build_page(url: str, response: httpx.Response, body: bytes) -> FetchedPage:
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
        has_pdf_signature = body.startswith(b"%PDF-")
        if mime == "application/pdf" or urlsplit(url).path.lower().endswith(".pdf"):
            if not has_pdf_signature:
                raise FetchError("fake_pdf")
            return FetchedPage(url, "pdf", body, None)
        if has_pdf_signature:
            return FetchedPage(url, "pdf", body, None)
        if mime in ("text/html", "application/xhtml+xml", ""):
            return FetchedPage(url, "html", body, response.encoding)
        raise FetchError("unsupported_content_type")
