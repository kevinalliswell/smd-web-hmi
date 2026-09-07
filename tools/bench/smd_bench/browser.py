"""Real browser actions and read-only observations against the installed HMI."""

import asyncio
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright


async def eventually(function, predicate, *, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        value = await function()
        if predicate(value):
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError("expected application or simulator state did not arrive")
        await asyncio.sleep(0.15)


class Browser:
    def __init__(self, evidence: Path, *, private_dir: Path | None = None, base="http://127.0.0.1:8000"):
        self.base, self.evidence = base, evidence
        self.private_dir, self.log_path = private_dir, None
        self.playwright = self.browser = self.context = self.page = None
        self.password = secrets.token_urlsafe(32)
        self.api_failures, self.page_errors = [], []
        self.expected_statuses: set[tuple[str, int]] = set()
        self.api_client = httpx.AsyncClient(base_url=base, trust_env=False, timeout=60)
        self.console_errors = []
        self.expected_console_errors = []
        self.expected_poll_disconnects = []
        self._service_stopped = False
        self._poll_failure_credits = 0
        self._pending_poll_console = []
        self.current_stage = "startup"

    async def open(self, *, diagnostic_logging=False):
        if self.private_dir is None:
            raise ValueError("browser requires owned private storage")
        private, evidence = self.private_dir.resolve(strict=True), self.evidence.resolve()
        if (
            not private.is_dir()
            or private == evidence
            or private.is_relative_to(evidence)
            or evidence.is_relative_to(private)
        ):
            raise ValueError("browser logs and public evidence must not overlap")
        self.log_path = private / ("chromium-" + secrets.token_hex(8) + ".log")
        self.log_path.touch(mode=0o600, exist_ok=False)
        environment = {key: value for key, value in os.environ.items() if key.casefold() != "chrome_log_file"}
        environment["CHROME_LOG_FILE"] = str(self.log_path)
        arguments = ["--enable-logging", "--log-file=" + str(self.log_path)]
        if diagnostic_logging:
            arguments.append("--v=1")
        if getattr(sys, "frozen", False):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(sys.executable).parent / "browsers")
        self.playwright = await async_playwright().start()
        # Full Chromium forwards logging handles to Windows child processes.
        self.browser = await self.playwright.chromium.launch(
            channel="chromium", headless=True, env=environment, args=arguments
        )
        self.context = await self.browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(30000)
        self.page.on("pageerror", lambda _error: self.page_errors.append("pageerror"))
        self.page.on("response", self._response)
        self.page.on("console", self._console)
        self.page.on("requestfailed", self._request_failed)

    def _is_control_poll(self, url):
        target, base = urlsplit(url), urlsplit(self.base)
        return (
            (target.scheme, target.netloc) == (base.scheme, base.netloc)
            and target.path == "/api/control"
            and not target.query
            and not target.fragment
        )

    @asynccontextmanager
    async def stopped_service(self):
        """Only the tool's explicit stop/restart interval permits a failed control poll."""
        if self._service_stopped:
            raise RuntimeError("nested stopped-service windows are not allowed")
        self._service_stopped = True
        try:
            yield

            async def health():
                try:
                    response = await self.api_client.get("/api/system/health", timeout=3)
                except httpx.RequestError:
                    return None
                response.raise_for_status()
                data = response.json().get("data", {})
                if data.get("status") == "ready" and any(
                    data.get("checks", {}).get(key) != "ok" for key in ("database", "schema", "storage", "backup")
                ):
                    raise AssertionError("restarted application's health checks failed")
                return data.get("status")

            await eventually(health, lambda status: status == "ready")
        finally:
            self._service_stopped = False
            self._poll_failure_credits = 0
            self._pending_poll_console.clear()

    def _request_failed(self, request):
        if (
            self._service_stopped
            and self._is_control_poll(request.url)
            and request.method == "GET"
            and request.failure == "net::ERR_CONNECTION_REFUSED"
        ):
            self.expected_poll_disconnects.append(
                {"path": "/api/control", "method": "GET", "stage": self.current_stage}
            )
            if self._pending_poll_console:
                row = self._pending_poll_console.pop(0)
                self.console_errors[:] = [item for item in self.console_errors if item is not row]
                row["kind"] = "poll_disconnect"
                self.expected_console_errors.append(row)
            else:
                self._poll_failure_credits += 1

    def _console(self, message):
        if message.type != "error":
            return
        path = urlsplit(message.location.get("url", "")).path
        status = re.search(r"server responded with a status of (\d{3})", message.text)
        expected = bool(status and (path, int(status[1])) in self.expected_statuses)
        if message.text.startswith("WebSocket connection") and self.current_stage in {
            "offline_pairing",
            "fault_host_service_restart",
        }:
            expected = True
        target = self.expected_console_errors if expected else self.console_errors
        row = {"kind": "http_failure" if status else "console_error", "stage": self.current_stage}
        if (
            self._service_stopped
            and self._is_control_poll(message.location.get("url", ""))
            and message.text == "Failed to load resource: net::ERR_CONNECTION_REFUSED"
        ):
            if self._poll_failure_credits:
                self._poll_failure_credits -= 1
                row["kind"] = "poll_disconnect"
                target = self.expected_console_errors
            else:
                self._pending_poll_console.append(row)
        target.append(row)

    def _response(self, response):
        path = urlsplit(response.url).path
        if path.startswith("/api/") and response.status >= 400:
            if (path, response.status) not in self.expected_statuses:
                self.api_failures.append({"path": path, "status": response.status, "stage": self.current_stage})

    async def login(self, username: str, password: str, *, page=None):
        page = page or self.page
        await page.goto(self.base + "/login")
        await page.get_by_label("用户名", exact=True).fill(username)
        await page.get_by_label("密码", exact=True).fill(password)
        await page.get_by_role("button", name="登录", exact=True).click()

    async def first_login(self, initial: str):
        await self.login("admin", initial)
        await self.page.get_by_role("heading", name="首次登录安全设置").wait_for()
        await self.page.get_by_label("当前密码", exact=True).fill(initial)
        await self.page.get_by_label("新密码", exact=True).fill(self.password)
        await self.page.get_by_label("确认新密码", exact=True).fill(self.password)
        await self.button("修改密码并重新登录").click()
        await self.page.get_by_text("密码已更新，请使用新密码登录。", exact=True).wait_for()
        await self.login("admin", self.password)
        await self.page.get_by_role("link", name=re.compile("^实验配方")).wait_for()
        await self.authenticate("admin", self.password)
        await self.shot("01-login")

    def button(self, name):
        return self.page.get_by_role("button", name=name, exact=True)

    async def go(self, route):
        await self.page.goto(self.base + route)

    async def shot(self, name):
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError("invalid evidence screenshot name")
        await self.page.screenshot(path=self.evidence / (name + ".png"), full_page=True)

    async def click_response(self, name, path, *, expected=200):
        async with self.page.expect_response(
            lambda response: urlsplit(response.url).path == path and response.request.method in {"POST", "PUT"},
            timeout=60000,
        ) as waiting:
            await self.button(name).click()
        response = await waiting.value
        if response.status != expected:
            raise AssertionError(f"HTTP status differed for {path}: {response.status}")
        return (await response.json()).get("data")

    async def authenticate(self, username, password):
        response = await self.api_client.post("/api/auth/login", json={"username": username, "password": password})
        if response.status_code != 200:
            raise AssertionError("independent test API login failed")
        self.api_client.headers["Authorization"] = "Bearer " + response.json()["data"]["token"]

    async def api(self, path, *, method="GET", body=None, expected=200, client=None):
        response = await (client or self.api_client).request(method, path, json=body)
        if response.status_code != expected:
            raise AssertionError(f"HTTP status differed for {path}: {response.status_code}")
        return response.json().get("data")

    async def ready(self):
        return await eventually(
            lambda: self.api("/api/status"),
            lambda status: status.get("system", {}).get("protocol_version") == "2.0"
            and status.get("control_ready") is True
            and all(
                status.get("_v2", {}).get(key) is True
                for key in ("online", "status_fresh", "alarms_reconciled", "profile_matches")
            ),
        )

    async def close(self):
        await self.api_client.aclose()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
