"""Log ownership at the launcher boundary; frozen self-check exercises real Chromium."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from smd_bench import browser as browser_module
from smd_bench import cli, windows
from smd_bench.browser import Browser
from windows_capabilities import requires_admin_owner


def browser_process_stub(monkeypatch, *, fail_content=False, write_log=True):
    """Use an actual child environment and files without installing a second browser."""
    calls = []

    async def nothing(*_args, **_kwargs):
        pass

    async def content(_text):
        if fail_content:
            raise RuntimeError("injected failure after browser launch")

    page = SimpleNamespace(set_default_timeout=lambda *_: None, on=lambda *_: None, set_content=content)

    async def new_page():
        return page

    context = SimpleNamespace(new_page=new_page, close=nothing)

    async def new_context(**_kwargs):
        return context

    async def launch(**kwargs):
        calls.append(kwargs)
        if write_log:
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os;from pathlib import Path;"
                    "Path(os.environ.get('CHROME_LOG_FILE','debug.log')).write_bytes(b'controlled child log\\n')",
                ],
                env=kwargs.get("env"),
                check=True,
            )
        return SimpleNamespace(new_context=new_context, close=nothing)

    async def start():
        return SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=nothing)

    monkeypatch.setattr(browser_module, "async_playwright", lambda: SimpleNamespace(start=start))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("inherit_foreign_log", [False, True])
async def test_browser_child_logs_stay_private_without_mutating_parent(tmp_path, monkeypatch, inherit_foreign_log):
    monkeypatch.chdir(tmp_path)
    private, evidence = tmp_path / "private", tmp_path / "evidence"
    private.mkdir(mode=0o700)
    evidence.mkdir()
    foreign = tmp_path / "foreign-debug.log"
    foreign.write_bytes(b"preexisting unrelated log\n")
    if inherit_foreign_log:
        monkeypatch.setenv("CHROME_LOG_FILE", str(foreign))
    else:
        monkeypatch.delenv("CHROME_LOG_FILE", raising=False)
    inherited = os.environ.get("CHROME_LOG_FILE")
    calls = browser_process_stub(monkeypatch)
    browser = Browser(evidence, private_dir=private)
    try:
        await browser.open()
    finally:
        await browser.close()
    logs = list(private.glob("*.log"))
    assert len(logs) == 1 and logs[0].read_bytes() == b"controlled child log\n"
    assert foreign.read_bytes() == b"preexisting unrelated log\n"
    assert not list(evidence.iterdir()) and not (tmp_path / "debug.log").exists()
    assert os.environ.get("CHROME_LOG_FILE") == inherited
    assert calls[0]["channel"] == "chromium"
    assert calls[0]["args"] == ["--enable-logging", "--log-file=" + str(logs[0])]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["missing", "evidence"])
async def test_browser_refuses_missing_or_public_log_storage_before_launch(tmp_path, monkeypatch, storage):
    calls = browser_process_stub(monkeypatch)
    browser = Browser(tmp_path, private_dir=tmp_path if storage == "evidence" else None)
    try:
        with pytest.raises(ValueError):
            await browser.open()
    finally:
        await browser.close()
    assert not calls and not list(tmp_path.iterdir())


@requires_admin_owner
@pytest.mark.parametrize("fail_content,write_log,expected", [(False, True, 0), (True, True, 1), (False, False, 1)])
def test_self_check_requires_private_log_and_cleans_only_its_temp_storage(
    tmp_path, monkeypatch, fail_content, write_log, expected
):
    monkeypatch.chdir(tmp_path)
    foreign = tmp_path / "foreign.log"
    foreign.write_bytes(b"unrelated log\n")
    monkeypatch.setenv("CHROME_LOG_FILE", str(foreign))
    monkeypatch.setattr(cli, "tool_manifest", lambda: {})
    monkeypatch.setattr(windows, "contain_child_processes", lambda: None)
    calls = browser_process_stub(monkeypatch, fail_content=fail_content, write_log=write_log)
    assert cli.main(["self-check"]) == expected
    assert len(calls) == 1
    log_path = Path(calls[0]["env"]["CHROME_LOG_FILE"])
    assert log_path.name.startswith("chromium-") and log_path.parent.name == "private"
    assert not log_path.parent.parent.exists()
    assert calls[0]["channel"] == "chromium"
    assert calls[0]["args"] == ["--enable-logging", "--log-file=" + str(log_path), "--v=1"]
    assert list(tmp_path.iterdir()) == [foreign]
    assert foreign.read_bytes() == b"unrelated log\n"
    assert os.environ["CHROME_LOG_FILE"] == str(foreign)
