"""File-level behavior of the standalone Windows PowerShell 5.1 reset tool."""

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/windows/reset-test-installation.ps1"


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def powershell():
    if os.name != "nt":
        pytest.skip("Requires real Windows PowerShell 5.1; never substitutes PowerShell 7")
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    assert executable.is_file()
    return executable


def run(powershell, tmp_path, body):
    wrapper = tmp_path / "check.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n"
        "if ($PSVersionTable.PSVersion.Major -ne 5) { throw 'Expected Windows PowerShell 5.1' }\n"
        f". {literal(SCRIPT)}\n{body}\n",
        encoding="utf-8-sig",
    )
    return subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_script_is_ascii_for_windows_powershell_51():
    assert SCRIPT.read_bytes().isascii()


@pytest.mark.parametrize("arguments", ["-Apply", "-ConfirmNoDeviceAttached"])
def test_partial_confirmation_never_reaches_machine_inspection(powershell, tmp_path, arguments):
    result = run(
        powershell,
        tmp_path,
        "function Get-ResetPlan { throw 'MACHINE_INSPECTION_MUST_NOT_RUN' }\n"
        f"Invoke-TestInstallationReset {arguments} | ConvertTo-Json -Depth 8",
    )
    assert result.returncode != 0
    assert "Both -Apply and -ConfirmNoDeviceAttached" in result.stderr
    assert "MACHINE_INSPECTION_MUST_NOT_RUN" not in result.stderr


def test_preview_only_reads_the_plan(powershell, tmp_path):
    result = run(
        powershell,
        tmp_path,
        "function Get-ResetPlan { return @{version='0.3.0-rc.2'; install_dir='C:\\program'; data_dir='C:\\data'} }\n"
        "function Enter-ResetMutex { throw 'MUTATION_MUST_NOT_RUN' }\n"
        "function New-PrivateDirectory { throw 'MUTATION_MUST_NOT_RUN' }\n"
        "Invoke-TestInstallationReset | ConvertTo-Json -Depth 8",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "mode": "preview",
        "phase": "validated",
        "version": "0.3.0-rc.2",
        "install_dir": "C:\\program",
        "data_dir": "C:\\data",
    }


@pytest.mark.parametrize(
    "name,phase",
    [
        ("install.json", "committed"),
        ("active.json", "migrating"),
        ("pairing.json", "prepared"),
        ("uninstall.json", "removing_service"),
    ],
)
def test_unfinished_transactions_are_refused_without_changing_files(powershell, tmp_path, name, phase):
    data = tmp_path / "data"
    (data / "updates").mkdir(parents=True)
    journal = data / "updates" / name
    journal.write_text(json.dumps({"phase": phase}), encoding="utf-8")
    before = journal.read_bytes()
    result = run(powershell, tmp_path, f"Assert-TransactionsClosed -DataDir {literal(data)}")
    assert result.returncode != 0
    assert journal.read_bytes() == before


def test_active_maintenance_ticket_is_never_deleted(powershell, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ticket = data / "maintenance.json"
    ticket.write_text('{"state":"prepared","token":"private-test-token"}', encoding="utf-8")
    before = ticket.read_bytes()
    result = run(powershell, tmp_path, f"Assert-TransactionsClosed -DataDir {literal(data)}")
    assert result.returncode != 0
    assert ticket.read_bytes() == before
    assert "private-test-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("configured", ["C:/external/smd.db", "db/smd.db", "${SMD_DATA_ROOT}/db/smd.db"])
def test_custom_or_ambiguous_database_is_refused(powershell, tmp_path, configured):
    env = tmp_path / "service.env"
    env.write_text("SMD_DB_PATH=" + json.dumps(configured) + "\n", encoding="utf-8")
    result = run(
        powershell,
        tmp_path,
        f"Assert-DefaultDatabase -EnvironmentFile {literal(env)} -ExpectedDatabase {literal(tmp_path / 'db/smd.db')}",
    )
    assert result.returncode != 0


@pytest.fixture
def trees(tmp_path):
    data, program, backup = (tmp_path / name for name in ("data", "program", "backup"))
    (data / "config").mkdir(parents=True)
    (data / "empty").mkdir()
    (data / "config/service.env").write_text("private fixture\n", encoding="utf-8")
    (data / "db").mkdir()
    (data / "db/smd.db").write_bytes(b"database fixture")
    program.mkdir()
    (program / "service.exe").write_bytes(b"program fixture")
    backup.mkdir()
    (backup / "metadata").mkdir()
    (backup / "metadata/service.json").write_text('{"name":"SmdHmi"}', encoding="utf-8")
    return data, program, backup


def test_full_backup_preserves_empty_directories_and_verifies_bytes(powershell, tmp_path, trees):
    data, program, backup = trees
    result = run(
        powershell,
        tmp_path,
        f"$plan=@{{data_dir={literal(data)};install_dir={literal(program)};version='0.3.0-rc.2'}}\n"
        f"New-VerifiedBackup -Plan $plan -BackupDir {literal(backup)}",
    )
    assert result.returncode == 0, result.stderr
    assert (backup / "data/config/service.env").read_bytes() == (data / "config/service.env").read_bytes()
    assert (backup / "data/db/smd.db").read_bytes() == (data / "db/smd.db").read_bytes()
    assert (backup / "data/empty").is_dir()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["version"] == "0.3.0-rc.2"
    assert any(item["path"] == "db/smd.db" and item["sha256"] for item in manifest["data"])
    assert "private fixture" not in result.stdout + result.stderr


def test_copy_corruption_fails_verification_and_keeps_originals(powershell, tmp_path, trees):
    data, program, backup = trees
    result = run(
        powershell,
        tmp_path,
        "function Copy-ResetTree { param($Source,$Destination)\n"
        "Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force\n"
        "[IO.File]::WriteAllText((Join-Path $Destination 'unexpected.txt'),'corrupt') }\n"
        f"$plan=@{{data_dir={literal(data)};install_dir={literal(program)};version='0.3.0-rc.2'}}\n"
        f"New-VerifiedBackup -Plan $plan -BackupDir {literal(backup)}",
    )
    assert result.returncode != 0
    assert not (backup / "manifest.json").exists()
    assert (data / "config/service.env").read_text(encoding="utf-8") == "private fixture\n"
    assert (program / "service.exe").read_bytes() == b"program fixture"
    assert backup.is_dir()


def test_failed_backup_never_removes_registration_or_retires_originals(powershell, tmp_path, trees):
    data, program, _ = trees
    backup_root = tmp_path / "reset-backups"
    result = run(
        powershell,
        tmp_path,
        f"function Get-ResetPlan {{ return @{{version='0.3.0-rc.2';data_dir={literal(data)};"
        f"install_dir={literal(program)};backup_root={literal(backup_root)}"
        "} }\n"
        "function Enter-ResetMutex { return (New-Object Threading.Mutex($false)) }\n"
        "function Export-ResetMetadata {}\nfunction Stop-ResetInstallation {}\n"
        "function New-VerifiedBackup { throw 'INJECTED_COPY_FAILURE' }\n"
        "function Remove-ResetRegistration { throw 'REGISTRATION_MUST_REMAIN' }\n"
        "function Move-RetiredTree { throw 'ORIGINALS_MUST_REMAIN' }\n"
        "try { Invoke-TestInstallationReset -Apply -ConfirmNoDeviceAttached; exit 99 }\n"
        "catch { if ($_.Exception.Message -ne 'INJECTED_COPY_FAILURE') { throw }; "
        "Write-Output $script:ResetBackup }",
    )
    assert result.returncode == 0, result.stderr
    backup = Path(result.stdout.strip())
    assert json.loads((backup / "stage.json").read_text(encoding="utf-8"))["phase"] == "copying"
    assert (data / "db/smd.db").read_bytes() == b"database fixture"
    assert (program / "service.exe").read_bytes() == b"program fixture"
    assert not (backup / "retired-data").exists()


def test_junction_is_rejected_without_following_or_changing_target(powershell, tmp_path, trees):
    data, program, _ = trees
    link = data / "junction"
    result = run(
        powershell,
        tmp_path,
        f"New-Item -ItemType Junction -Path {literal(link)} -Target {literal(program)} | Out-Null\n"
        f"try {{ Assert-NoReparse {literal(data)}; exit 99 }}\n"
        "catch { if ($_.Exception.Message -notlike '*Reparse*') { throw } }\n"
        f"finally {{ [IO.Directory]::Delete({literal(link)}) }}",
    )
    assert result.returncode == 0, result.stderr
    assert (program / "service.exe").read_bytes() == b"program fixture"
