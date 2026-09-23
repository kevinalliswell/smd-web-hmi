import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from smd_bench import browser, cli, windows


@pytest.mark.parametrize("ci_run_id", ["12345", None])
def test_acceptance_records_actual_ci_run_or_null_for_field_execution(tmp_path, monkeypatch, ci_run_id):
    monkeypatch.setattr(
        cli, "os", SimpleNamespace(name="nt", environ={"GITHUB_RUN_ID": ci_run_id} if ci_run_id else {})
    )
    monkeypatch.setattr(cli, "tool_manifest", lambda: {})
    manifest = SimpleNamespace(version="0.3.0", commit="a" * 40)
    monkeypatch.setattr(cli, "verify_installer", lambda *args: (manifest, "b" * 64))
    monkeypatch.setattr(
        cli,
        "preflight",
        lambda: {"status": "ready", "program_files": tmp_path / "program", "program_data": tmp_path / "data"},
    )
    monkeypatch.setattr(windows, "contain_child_processes", lambda: None)

    def prepare(self, evidence, *args):
        evidence.mkdir()
        self.evidence_created = True

    monkeypatch.setattr(cli.Installation, "prepare", prepare)
    monkeypatch.setattr(cli.Installation, "install_package", lambda *args: None)
    monkeypatch.setattr(cli, "wait_health", AsyncMock(return_value={"version": "0.3.0"}))
    monkeypatch.setattr(cli, "run_scenarios", AsyncMock())
    args = SimpleNamespace(
        installer=tmp_path / "installer.exe",
        manifest=tmp_path / "manifest.json",
        evidence=tmp_path / "evidence",
        run_id="c" * 32,
        scenario="all",
    )
    assert cli.run(args) == 0
    result = json.loads((args.evidence / "acceptance.json").read_text())
    assert result["ci_run_id"] == ci_run_id


@pytest.mark.asyncio
async def test_failed_browser_check_records_its_sanitized_rows(tmp_path):
    ui = browser.Browser(tmp_path)
    try:
        ui.current_stage = "installer_offline_repair"
        ui._response(SimpleNamespace(url=ui.base + "/api/control?view=secret", status=503))
        result = {}
        with pytest.raises(AssertionError):
            cli.check_browser_observations(ui, result)
        assert result["unexpected_api_errors"] == 1
        assert result["unexpected_browser_observations"]["api_failures"] == [
            {"path": "/api/control", "status": 503, "stage": "installer_offline_repair"}
        ]
    finally:
        await ui.close()


@pytest.mark.asyncio
async def test_clean_browser_check_records_counts_only(tmp_path):
    ui = browser.Browser(tmp_path)
    try:
        result = {}
        cli.check_browser_observations(ui, result)
        assert result["browser_errors"] == result["unexpected_console_errors"] == 0
        assert "unexpected_browser_observations" not in result
    finally:
        await ui.close()


@pytest.mark.asyncio
async def test_scenario_failure_keeps_the_browser_rows_seen_so_far(tmp_path, monkeypatch):
    class FailingBrowser(browser.Browser):
        async def open(self, **_):
            self._console(
                SimpleNamespace(type="error", location={"url": self.base + "/assets/app.js"}, text="Uncaught secret")
            )
            raise TimeoutError("stage did not arrive")

    monkeypatch.setattr(browser, "Browser", FailingBrowser)
    result = {"evidence_dir": str(tmp_path)}
    with pytest.raises(TimeoutError):
        await cli.run_scenarios(SimpleNamespace(private=tmp_path), result, "all")
    assert result["unexpected_browser_observations"]["console_errors"] == [
        {"kind": "console_error", "stage": "startup", "path": "/assets/app.js", "code": "other"}
    ]
