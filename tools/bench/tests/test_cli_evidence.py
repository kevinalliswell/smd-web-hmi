import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from smd_bench import cli, windows


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
