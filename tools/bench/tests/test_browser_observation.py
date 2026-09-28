import asyncio
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
    browser.poll_settle_timeout = 0.1
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
    browser.poll_settle_timeout = 0.1
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


def refused_poll(browser):
    message = SimpleNamespace(
        type="error",
        location={"url": browser.base + "/api/control"},
        text="Failed to load resource: net::ERR_CONNECTION_REFUSED",
    )
    request = SimpleNamespace(url=browser.base + "/api/control", method="GET", failure="net::ERR_CONNECTION_REFUSED")
    return message, request


@pytest.mark.asyncio
async def test_stopped_service_waits_for_a_late_failure_report(tmp_path):
    browser = Browser(tmp_path)
    await browser.api_client.aclose()
    browser.api_client = httpx.AsyncClient(
        base_url=browser.base, transport=httpx.MockTransport(lambda _: healthy_response())
    )
    message, request = refused_poll(browser)

    async def late_report():
        await asyncio.sleep(0.2)
        browser._request_failed(request)

    try:
        async with browser.stopped_service():
            browser._request_started(request)
            browser._console(message)  # The echo arrives first while its proof is still in flight.
            report = asyncio.create_task(late_report())
        await report
        assert not browser.console_errors
        assert len(browser.expected_poll_disconnects) == 1
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_stopped_service_still_rejects_an_unproven_echo_once_the_settle_bound_passes(tmp_path):
    browser = Browser(tmp_path)
    browser.poll_settle_timeout = 0.1
    await browser.api_client.aclose()
    browser.api_client = httpx.AsyncClient(
        base_url=browser.base, transport=httpx.MockTransport(lambda _: healthy_response())
    )
    message, request = refused_poll(browser)
    try:
        async with browser.stopped_service():
            browser._request_started(request)
            browser._console(message)
        assert len(browser.console_errors) == 1 and not browser.expected_poll_disconnects
        browser._request_failed(request)  # A report after the window closed cannot authorize the echo.
        assert len(browser.console_errors) == 1 and not browser.expected_poll_disconnects
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_console_rows_keep_a_path_and_code_but_never_message_text(tmp_path):
    browser = Browser(tmp_path)
    try:
        for url, text in [
            (browser.base + "/api/status?token=secret", "Failed to load resource: net::ERR_CONNECTION_RESET secret"),
            (browser.base + "/api/status", "Failed to load resource: the server responded with a status of 503 (x)"),
            (browser.base + "/assets/app.js", "WebSocket connection to 'ws://127.0.0.1:8000/ws?token=secret' failed"),
            (browser.base + "/assets/app.js", "Uncaught secret value"),
        ]:
            browser._console(SimpleNamespace(type="error", location={"url": url}, text=text))
        assert [(row["path"], row["code"]) for row in browser.console_errors] == [
            ("/api/status", "net::ERR_CONNECTION_RESET"),
            ("/api/status", "http_503"),
            ("/assets/app.js", "websocket"),
            ("/assets/app.js", "other"),
        ]
        assert "secret" not in str(browser.unexpected_observations())
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_page_error_rows_keep_only_a_plain_error_name(tmp_path):
    browser = Browser(tmp_path)
    try:
        browser.current_stage = "recipe_ui"
        browser._page_error(SimpleNamespace(name="TypeError", message="secret detail"))
        browser._page_error(SimpleNamespace(name="secret<script>", message="secret"))
        assert browser.page_errors == [
            {"kind": "pageerror", "stage": "recipe_ui", "name": "TypeError"},
            {"kind": "pageerror", "stage": "recipe_ui", "name": "Error"},
        ]
    finally:
        await browser.close()
