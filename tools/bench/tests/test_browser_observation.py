from types import SimpleNamespace

import pytest
from smd_bench.browser import Browser


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
