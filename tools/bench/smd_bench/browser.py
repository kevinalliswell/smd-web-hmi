"""Real browser actions and read-only observations against the installed HMI."""

import asyncio
import os
import re
import secrets
import sys
import time
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
    def __init__(self, evidence: Path, *, base="http://127.0.0.1:8000"):
        self.base, self.evidence = base, evidence
        self.playwright = self.browser = self.context = self.page = None
        self.password = secrets.token_urlsafe(32)
        self.api_failures, self.page_errors = [], []
        self.expected_statuses: set[tuple[str, int]] = set()
        self.api_client = httpx.AsyncClient(base_url=base, trust_env=False, timeout=60)
        self.console_errors = []

    async def open(self):
        if getattr(sys, "frozen", False):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(sys.executable).parent / "browsers")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(30000)
        self.page.on("pageerror", lambda _error: self.page_errors.append("pageerror"))
        self.page.on("response", self._response)
        self.page.on(
            "console", lambda message: self.console_errors.append("console_error") if message.type == "error" else None
        )

    def _response(self, response):
        path = urlsplit(response.url).path
        if path.startswith("/api/") and response.status >= 400:
            if (path, response.status) not in self.expected_statuses:
                self.api_failures.append({"path": path, "status": response.status})

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
