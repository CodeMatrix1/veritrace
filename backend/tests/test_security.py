import httpx
import pymupdf
import pytest

from components.text_extractor import ExtractionError, extract_pdf
from services.url_safety import FetchError, SafeFetcher, UnsafeURL, is_unsafe_ip, precheck_url
from tests.conftest import public_resolver


def fetcher(handler, resolver=public_resolver) -> SafeFetcher:
    return SafeFetcher(transport=httpx.MockTransport(handler), resolver=resolver)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fc00::1",
    "224.0.0.1", "::ffff:10.0.0.1", "0.0.0.0",
])
def test_unsafe_ips(ip):
    assert is_unsafe_ip(ip)


def test_public_ip_is_allowed():
    assert not is_unsafe_ip("93.184.216.34")


def test_precheck_blocks_literal_private_ip_and_localhost():
    assert precheck_url("http://127.0.0.1/admin") == "unsafe_destination"
    assert precheck_url("http://localhost/x") == "unsafe_destination"
    assert precheck_url("file:///etc/passwd") == "invalid_url"
    assert precheck_url("https://a.com/video.mp4") == "unsupported_file_type"


@pytest.mark.asyncio
async def test_direct_private_ip_is_blocked():
    def handler(request):
        raise AssertionError("must not connect")

    with pytest.raises(UnsafeURL):
        await fetcher(handler).fetch("http://10.0.0.5/")


@pytest.mark.asyncio
async def test_hostname_resolving_to_private_ip_is_blocked():
    async def private(host):
        return ["10.0.0.9"]

    def handler(request):
        raise AssertionError("must not connect")

    with pytest.raises(UnsafeURL):
        await fetcher(handler, private).fetch("https://sneaky.example.com/")


@pytest.mark.asyncio
async def test_redirect_to_private_ip_is_blocked():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    with pytest.raises(UnsafeURL):
        await fetcher(handler).fetch("https://example.com/")
    assert calls == ["https://example.com/"]


@pytest.mark.asyncio
async def test_redirect_limit():
    def handler(request):
        return httpx.Response(302, headers={"location": "/next"})

    with pytest.raises(FetchError, match="too_many_redirects"):
        await fetcher(handler).fetch("https://example.com/")


@pytest.mark.asyncio
async def test_large_response_stops_during_streaming():
    pulled = []

    async def chunks():
        for _ in range(100):
            pulled.append(1)
            yield b"x" * (1024 * 1024)

    def handler(request):
        return httpx.Response(200, content=chunks(), headers={"content-type": "text/html"})

    with pytest.raises(FetchError, match="too_large"):
        await fetcher(handler).fetch("https://example.com/")
    assert len(pulled) < 10


@pytest.mark.asyncio
async def test_fake_pdf_is_rejected():
    def handler(request):
        return httpx.Response(
            200, content=b"<html>nope</html>", headers={"content-type": "application/pdf"}
        )

    with pytest.raises(FetchError, match="fake_pdf"):
        await fetcher(handler).fetch("https://example.com/report.pdf")


@pytest.mark.asyncio
async def test_real_pdf_extracts():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "India GDP grew by 7 percent in the fiscal year.")
    body = doc.tobytes()

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    page = await fetcher(handler).fetch("https://example.com/r.pdf")
    assert page.content_type == "pdf"
    assert "India GDP" in extract_pdf(page.body).text


def test_image_only_pdf_is_unreadable():
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ExtractionError, match="unreadable_pdf"):
        extract_pdf(doc.tobytes())
