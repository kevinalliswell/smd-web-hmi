"""File-level behavior of the standalone Windows PowerShell 5.1 reset tool."""

import json
import math
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/windows/reset-test-installation.ps1"
SMOKE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/test-reset-smoke.ps1"


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


@pytest.mark.parametrize(
    "arguments,accepted",
    [
        ('--recover --install "C:\\Program Files\\SmdHmi"', True),
        ('--recover --non-interactive --install "C:\\Program Files\\SmdHmi"', True),
        ('--recover --non-interactive --install "C:\\Another App"', False),
        ('--recover --install "C:\\Program Files\\SmdHmi" --force', False),
        ('--recover --non-interactive --install "C:\\Program Files\\SmdHmi" --force', False),
        ("--recover --install C:\\Program Files\\SmdHmi", False),
        ('--RECOVER --install "C:\\Program Files\\SmdHmi"', False),
        ('--recover --non-interactive --install "C:\\Program Files\\SmdHmi"; whoami', False),
    ],
)
def test_recovery_task_accepts_only_exact_owned_released_arguments(powershell, tmp_path, arguments, accepted):
    result = run(
        powershell,
        tmp_path,
        f"Test-ResetRecoveryArguments -Arguments {literal(arguments)} "
        "-InstallDir 'C:\\Program Files\\SmdHmi' | ConvertTo-Json",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is accepted


@pytest.mark.parametrize(
    "status,error_code,ticket,accepted",
    [
        (410, "http_error", False, True),
        (503, "maintenance_active", False, False),
        (200, "http_error", False, False),
        (410, "maintenance_active", False, False),
        (410, "http_error", True, False),
    ],
)
def test_reset_smoke_requires_retired_api_without_maintenance_ticket(
    powershell, tmp_path, status, error_code, ticket, accepted
):
    data = tmp_path / "data"
    data.mkdir()
    maintenance = data / "maintenance.json"
    if ticket:
        maintenance.write_text('{"state":"prepared"}', encoding="utf-8")
    content = json.dumps({"error_code": error_code, "message": "Use the Windows installer"})
    result = run(
        powershell,
        tmp_path,
        f". {literal(SMOKE_SCRIPT)}\n"
        f"$response=@{{StatusCode={status}; Content={literal(content)}}}\n"
        f"Assert-RetiredMaintenancePrepare -Response $response -DataDir {literal(data)}",
    )
    assert (result.returncode == 0) is accepted
    assert maintenance.exists() is ticket
    if ticket:
        assert maintenance.read_text(encoding="utf-8") == '{"state":"prepared"}'


def test_stage_file_can_be_atomically_updated_twice_in_windows_powershell_51(powershell, tmp_path):
    stage = tmp_path / "阶段 状态.json"
    result = run(
        powershell,
        tmp_path,
        f"$stage={literal(stage)}\n"
        "foreach ($phase in @('planned','stopping')) {\n"
        "Write-ResetJson -Path $stage -Value @{phase=$phase}\n"
        "if ((Read-ResetJson $stage).phase -cne $phase) { throw 'Stage readback did not match' }\n"
        "}\n(Read-ResetJson $stage).phase",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "stopping"
    assert json.loads(stage.read_text(encoding="utf-8")) == {"phase": "stopping"}
    assert not Path(str(stage) + ".tmp").exists()


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
    assert not (backup / "stage.json").exists()
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


def test_retired_tree_removes_service_ownership_and_access(powershell, tmp_path, trees):
    data, _, backup = trees
    retired = backup / "retired-data"
    result = run(
        powershell,
        tmp_path,
        f"$file={literal(data / 'config/service.env')}\n"
        "Invoke-ResetNative (Join-Path $env:SystemRoot 'System32/icacls.exe') @($file,'/setowner','*S-1-5-19','/Q')\n"
        "$acl=Get-Acl -LiteralPath $file\n"
        "$sid=New-Object Security.Principal.SecurityIdentifier('S-1-5-19')\n"
        "if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { throw 'Fixture owner not set' }\n"
        "$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow')))\n"
        "Set-Acl -LiteralPath $file -AclObject $acl\n"
        f"Move-RetiredTree -Source {literal(data)} -Destination {literal(retired)}\n"
        f"$acl=Get-Acl -LiteralPath {literal(retired / 'config/service.env')}\n"
        "if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-544') { throw 'Owner retained' }\n"
        "foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {\n"
        "if ($rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Service access retained' } }",
    )
    assert result.returncode == 0, result.stderr
    assert not data.exists()
    assert (retired / "config/service.env").read_text(encoding="utf-8") == "private fixture\n"


@pytest.mark.parametrize(
    "stage,expected",
    [
        ({"schema_version": 1, "phase": phase}, phase)
        for phase in (
            "planned",
            "stopping",
            "copying",
            "verified",
            "removing_registration",
            "retiring_program",
            "retiring_data",
            "completed",
        )
    ]
    + [
        ({"schema_version": 1, "phase": "DO_NOT_ECHO"}, "unknown"),
        ({"schema_version": 1, "phase": ["copying"]}, "unknown"),
        ({"schema_version": 1, "phase": "COPYING"}, "unknown"),
        ({"schema_version": 1}, "unknown"),
        ({"schema_version": True, "phase": "copying"}, "unknown"),
        ({"schema_version": "1", "phase": "copying"}, "unknown"),
        ({"schema_version": 2, "phase": "copying"}, "unknown"),
        ({"phase": "copying"}, "unknown"),
    ],
)
def test_smoke_reset_phase_serializes_only_a_fixed_enum(powershell, tmp_path, stage, expected):
    stage = {**stage, "password": "DO_NOT_ECHO", "install_dir": str(tmp_path)}
    result = run(
        powershell,
        tmp_path,
        f". {literal(SMOKE_SCRIPT)}\n"
        f"$Stage={literal(json.dumps(stage))} | ConvertFrom-Json\n"
        "Get-TestResetPhase -StageRecord $Stage | ConvertTo-Json",
    )
    assert result.returncode == 0
    assert not result.stderr
    assert json.loads(result.stdout) == expected
    assert "DO_NOT_ECHO" not in result.stdout
    assert str(tmp_path) not in result.stdout


def test_smoke_reset_phase_read_failure_is_redacted(powershell, tmp_path):
    result = run(
        powershell,
        tmp_path,
        f". {literal(SMOKE_SCRIPT)}\n"
        "$Stage=New-Object PSObject\n"
        "Add-Member -InputObject $Stage -MemberType ScriptProperty -Name schema_version "
        "-Value { throw 'DO_NOT_ECHO' }\n"
        "Get-TestResetPhase -StageRecord $Stage | ConvertTo-Json",
    )
    assert result.returncode == 0
    assert not result.stderr
    assert json.loads(result.stdout) == "unknown"


_BACKUP_STEPS = (
    "program_source_snapshot",
    "program_copy",
    "program_protect",
    "program_destination_snapshot",
    "program_source_recheck",
    "data_source_snapshot",
    "data_copy",
    "data_protect",
    "data_destination_snapshot",
    "data_source_recheck",
    "metadata_snapshot",
    "manifest_write",
    "manifest_readback",
)
_TIMING_CONTEXT = (
    "$timing=@{current_step=$null;completed_steps=[Collections.Generic.List[object]]::new();"
    "timer=[Diagnostics.Stopwatch]::new()}\n"
)


@pytest.mark.parametrize(
    "case",
    [
        "first_step",
        "running",
        "completed",
        "extra_private_fields",
        "duplicate",
        "out_of_order",
        "too_many",
        "negative",
        "nan",
        "positive_infinity",
        "negative_infinity",
        "bool",
        "numeric_string",
        "unknown_current",
        "unknown_completed",
        "current_already_completed",
        "premature_completed",
        "missing",
        "non_array",
    ],
)
def test_backup_timing_projection_rejects_invalid_data_and_drops_private_fields(powershell, tmp_path, case):
    completed = [{"name": _BACKUP_STEPS[0], "elapsed_seconds": 1.25}]
    operation = {"current_step": _BACKUP_STEPS[1], "completed_steps": completed}
    if case == "first_step":
        operation = {"current_step": _BACKUP_STEPS[0], "completed_steps": []}
    elif case == "completed":
        operation = {
            "current_step": None,
            "completed_steps": [{"name": step, "elapsed_seconds": 0} for step in _BACKUP_STEPS],
        }
    elif case == "extra_private_fields":
        operation["secret"] = "DO_NOT_ECHO"
        completed[0]["path"] = str(tmp_path)
        completed[0]["password"] = "DO_NOT_ECHO"
    elif case == "duplicate":
        completed.append(completed[0].copy())
    elif case == "out_of_order":
        completed[0]["name"] = _BACKUP_STEPS[1]
    elif case == "too_many":
        completed[:] = [{"name": _BACKUP_STEPS[0], "elapsed_seconds": 0}] * 14
    elif case == "negative":
        completed[0]["elapsed_seconds"] = -1
    elif case == "bool":
        completed[0]["elapsed_seconds"] = True
    elif case == "numeric_string":
        completed[0]["elapsed_seconds"] = "1.25"
    elif case == "unknown_current":
        operation["current_step"] = "DO_NOT_ECHO"
    elif case == "unknown_completed":
        completed[0]["name"] = "DO_NOT_ECHO"
    elif case == "current_already_completed":
        operation["current_step"] = _BACKUP_STEPS[0]
    elif case == "premature_completed":
        operation["current_step"] = None
    elif case == "non_array":
        operation["completed_steps"] = completed[0]
    stage = {"schema_version": 1, "phase": "copying", "backup_operation": operation}
    if case == "missing":
        del stage["backup_operation"]
    mutate = ""
    if case in {"nan", "positive_infinity", "negative_infinity"}:
        member = {"nan": "NaN", "positive_infinity": "PositiveInfinity", "negative_infinity": "NegativeInfinity"}[case]
        mutate = f"$stage.backup_operation.completed_steps[0].elapsed_seconds=[double]::{member}\n"
    result = run(
        powershell,
        tmp_path,
        f". {literal(SMOKE_SCRIPT)}\n"
        f"$stage={literal(json.dumps(stage))} | ConvertFrom-Json\n"
        + mutate
        + "$safe=Get-TestResetBackupOperation -StageRecord $stage\n"
        "ConvertTo-Json -InputObject $safe -Depth 6",
    )
    assert result.returncode == 0
    assert not result.stderr
    if case in {"first_step", "completed"}:
        expected = operation
    elif case in {"running", "extra_private_fields"}:
        expected = {
            "current_step": _BACKUP_STEPS[1],
            "completed_steps": [{"name": _BACKUP_STEPS[0], "elapsed_seconds": 1.25}],
        }
    else:
        expected = None
    assert json.loads(result.stdout) == expected
    assert "DO_NOT_ECHO" not in result.stdout
    assert str(tmp_path) not in result.stdout


def test_real_backup_records_all_thirteen_steps_without_exposing_source_contents(powershell, tmp_path, trees):
    data, program, backup = trees
    result = run(
        powershell,
        tmp_path,
        f". {literal(SMOKE_SCRIPT)}\n"
        f"$plan=@{{data_dir={literal(data)};install_dir={literal(program)};version='0.3.0'}}\n"
        + _TIMING_CONTEXT
        + f"$hash=New-VerifiedBackup $plan {literal(backup)} $timing\n"
        + "if ($hash -isnot [string] -or $hash -cnotmatch '^[0-9a-f]{64}$') { throw 'Backup return changed' }\n"
        + f"Set-ResetStage 'completed' $plan {literal(backup)} $timing\n"
        + f"$record=Read-ResetJson {literal(backup / 'stage.json')}\n"
        + "Get-TestResetBackupOperation $record | ConvertTo-Json -Depth 6",
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    progress = json.loads(result.stdout)
    assert progress["current_step"] is None
    assert [item["name"] for item in progress["completed_steps"]] == list(_BACKUP_STEPS)
    assert all(
        isinstance(item["elapsed_seconds"], (int, float))
        and math.isfinite(item["elapsed_seconds"])
        and item["elapsed_seconds"] >= 0
        for item in progress["completed_steps"]
    )
    stage = json.loads((backup / "stage.json").read_text(encoding="utf-8"))
    assert stage["phase"] == "completed"
    assert (backup / "data/db/smd.db").read_bytes() == (data / "db/smd.db").read_bytes()
    assert (backup / "data/empty").is_dir()
    assert "private fixture" not in result.stdout
    assert str(tmp_path) not in result.stdout


@pytest.mark.parametrize("copy_fails", [False, True])
def test_timing_write_failure_preserves_original_backup_error_or_success(powershell, tmp_path, trees, copy_fails):
    data, program, backup = trees
    body = (
        "Rename-Item function:Write-ResetJson Write-OriginalResetJson\n"
        "$script:TimingFailures=0\n"
        "function Write-ResetJson { param($Path,$Value)\n"
        "if ($Value.Contains('backup_operation')) { $script:TimingFailures++; throw 'DIAGNOSTIC_ONLY_FAILURE' }\n"
        "Write-OriginalResetJson -Path $Path -Value $Value }\n"
        f"$plan=@{{data_dir={literal(data)};install_dir={literal(program)};version='0.3.0'}}\n"
        + _TIMING_CONTEXT
        + f"Set-ResetStage 'copying' $plan {literal(backup)}\n"
    )
    if copy_fails:
        body += (
            "function Copy-ResetTree { param($Source,$Destination); throw 'ORIGINAL_COPY_FAILURE' }\n"
            f"try {{ New-VerifiedBackup $plan {literal(backup)} $timing; throw 'BACKUP_SHOULD_FAIL' }}\n"
            "catch { if ($_.Exception.Message -cne 'ORIGINAL_COPY_FAILURE') { throw } }\n"
        )
    else:
        body += (
            f"$hash=New-VerifiedBackup $plan {literal(backup)} $timing\n"
            f"Set-ResetStage 'completed' $plan {literal(backup)} $timing\n"
        )
    body += (
        "if ($script:TimingFailures -lt 1) { throw 'Timing failure was not exercised' }\n'original_result_preserved'"
    )
    result = run(powershell, tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert result.stdout.strip() == "original_result_preserved"
    record = json.loads((backup / "stage.json").read_text(encoding="utf-8"))
    assert record["phase"] == ("copying" if copy_fails else "completed")
    assert "backup_operation" not in record
    assert (data / "config/service.env").read_text(encoding="utf-8") == "private fixture\n"
    assert (program / "service.exe").read_bytes() == b"program fixture"
    assert (backup / "manifest.json").exists() is not copy_fails
