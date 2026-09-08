from types import SimpleNamespace

import httpx
import pytest
from smd_bench.browser import Browser


def healthy_response():
    return httpx.Response(
        200,
        json={"data": {"status": "ready", "checks": dict.fromkeys(("database", "schema", "storage", "backup"), "ok")}},
    )


@pytest.mark.asyncio
async def test_console_observations_only_allow_declared_faults(tmp_path):
    browser = Browser(tmp_path)
    try:
        browser.expected_statuses.add(("/api/commands", 504))
        browser._console(
            SimpleNamespace(
                type="error",
                location={"url": "http://127.0.0.1:8000/api/commands"},
                text="Failed to load resource: the server responded with a status of 504 (Gateway Timeout)",
            )
        )
        assert len(browser.expected_console_errors) == 1
        assert not browser.console_errors
        browser._console(
            SimpleNamespace(
                type="error",
                location={"url": "http://127.0.0.1:8000/api/status"},
                text="Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
            )
        )
        browser._console(SimpleNamespace(type="error", location={}, text="Secret token in an unexpected UI message"))
        assert len(browser.console_errors) == 2
        assert all("Secret" not in str(row) for row in browser.console_errors)
    finally:
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("console_first", [True, False])
async def test_stopped_service_poll_requires_matching_request_and_ready(tmp_path, console_first):
    browser = Browser(tmp_path)
    await browser.api_client.aclose()
    health_calls = []

    def health(request):
        health_calls.append(request.url.path)
        return healthy_response()

    browser.api_client = httpx.AsyncClient(base_url=browser.base, transport=httpx.MockTransport(health))
    message = SimpleNamespace(
        type="error",
        location={"url": browser.base + "/api/control"},
        text="Failed to load resource: net::ERR_CONNECTION_REFUSED",
    )
    request = SimpleNamespace(url=browser.base + "/api/control", method="GET", failure="net::ERR_CONNECTION_REFUSED")
    try:
        async with browser.stopped_service():
            if console_first:
                browser._console(message)
                assert len(browser.console_errors) == 1  # A console string alone cannot authorize it.
                browser._request_failed(request)
            else:
                browser._request_failed(request)
                browser._console(message)
        assert health_calls == ["/api/system/health"]
        assert not browser.console_errors
        assert len(browser.expected_poll_disconnects) == 1
        browser._console(message)  # Outside the verified stop/restart window it is unexpected.
        browser._request_failed(request)
        assert len(browser.console_errors) == 1 and len(browser.expected_poll_disconnects) == 1
    finally:
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,method,failure",
    [
        ("http://other.invalid/api/control", "GET", "net::ERR_CONNECTION_REFUSED"),
        ("http://127.0.0.1:8000/api/status", "GET", "net::ERR_CONNECTION_REFUSED"),
        ("http://127.0.0.1:8000/api/control", "POST", "net::ERR_CONNECTION_REFUSED"),
        ("http://127.0.0.1:8000/api/control", "GET", "net::ERR_CONNECTION_RESET"),
    ],
)
async def test_stopped_service_does_not_allow_other_network_failures(tmp_path, url, method, failure):
    browser = Browser(tmp_path)
    await browser.api_client.aclose()
    browser.api_client = httpx.AsyncClient(
        base_url=browser.base, transport=httpx.MockTransport(lambda _: healthy_response())
    )
    try:
        async with browser.stopped_service():
            browser._console(
                SimpleNamespace(type="error", location={"url": url}, text="Failed to load resource: " + failure)
            )
            browser._request_failed(SimpleNamespace(url=url, method=method, failure=failure))
            browser._response(SimpleNamespace(url=browser.base + "/api/control", status=503))
        assert len(browser.console_errors) == 1 and len(browser.api_failures) == 1
        assert not browser.expected_poll_disconnects
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_stopped_service_keeps_unproven_console_error(tmp_path):
    browser = Browser(tmp_path)
    await browser.api_client.aclose()
    browser.api_client = httpx.AsyncClient(
        base_url=browser.base, transport=httpx.MockTransport(lambda _: healthy_response())
    )
    try:
        async with browser.stopped_service():
            browser._console(
                SimpleNamespace(
                    type="error",
                    location={"url": browser.base + "/api/control"},
                    text="Failed to load resource: net::ERR_CONNECTION_REFUSED",
                )
            )
        assert len(browser.console_errors) == 1 and not browser.expected_poll_disconnects
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_stopped_service_fails_if_ready_check_fails(tmp_path):
    browser = Browser(tmp_path)
    await browser.api_client.aclose()
    browser.api_client = httpx.AsyncClient(
        base_url=browser.base, transport=httpx.MockTransport(lambda _: httpx.Response(503))
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            async with browser.stopped_service():
                pass
    finally:
        await browser.close()
